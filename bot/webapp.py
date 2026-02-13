"""FastAPI webapp server for Telegram Mini App integration."""

import hashlib
import hmac
import json
import logging
from urllib.parse import parse_qs, unquote

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .config import config
from .store import get_store
from .client import get_client
from .strings import gstr

logger = logging.getLogger(__name__)

app = FastAPI(title="Incognitus WebApp API", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


def validate_init_data(init_data: str) -> dict | None:
    """Validate Telegram WebApp initData using HMAC-SHA256.

    Returns the parsed user dict on success, None on failure.
    """
    try:
        parsed = parse_qs(init_data, keep_blank_values=True)
        received_hash = parsed.get("hash", [None])[0]
        if not received_hash:
            logger.warning("initData missing hash param")
            return None

        # Build data-check-string: sorted key=value pairs excluding hash
        pairs = []
        for key, values in parsed.items():
            if key == "hash":
                continue
            pairs.append(f"{key}={values[0]}")
        pairs.sort()
        data_check_string = "\n".join(pairs)

        # HMAC-SHA256: secret = HMAC("WebAppData", bot_token)
        secret_key = hmac.new(
            b"WebAppData", config.bot_token.encode(), hashlib.sha256
        ).digest()
        computed_hash = hmac.new(
            secret_key, data_check_string.encode(), hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(computed_hash, received_hash):
            logger.warning(f"initData HMAC mismatch: computed={computed_hash[:16]}... received={received_hash[:16]}...")
            return None

        user_json = parsed.get("user", [None])[0]
        if not user_json:
            logger.warning("initData missing user param")
            return None

        return json.loads(unquote(user_json))
    except Exception:
        logger.exception("initData validation failed")
        return None


def get_user_from_init_data(request: Request) -> dict:
    """Extract and validate user from X-Init-Data header."""
    init_data = request.headers.get("X-Init-Data", "")
    if not init_data:
        raise HTTPException(status_code=401, detail="Missing initData")
    logger.info(f"Validating initData ({len(init_data)} chars)")
    user = validate_init_data(init_data)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid initData")
    logger.info(f"Authenticated user: {user.get('id')}")
    return user


# ---- Models ----

class SendMessageRequest(BaseModel):
    token: str
    text: str


# ---- Endpoints ----

@app.get("/api/health")
def health():
    """Health check."""
    return {"status": "ok", "port": config.webapp_port}


@app.get("/api/link/{token}")
def get_link_info(token: str):
    """Get recipient nickname for share page."""
    store = get_store()

    # Try regular token first
    target_id, target_data = store.get_by_token(token)

    # Try temp link if not found
    if not target_data:
        target_id, target_data = store.get_user_by_temp_link(token)

    if not target_data:
        raise HTTPException(status_code=404, detail="Link not found")

    return {"nickname": target_data["nickname"]}


@app.post("/api/send")
async def send_message(body: SendMessageRequest):
    """Send anonymous message from share page."""
    store = get_store()

    if not body.text or not body.text.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    if len(body.text) > 4096:
        raise HTTPException(status_code=400, detail="Message too long (max 4096)")

    # Resolve token
    target_id, target_data = store.get_by_token(body.token)
    is_temp = False
    if not target_data:
        target_id, target_data = store.get_user_by_temp_link(body.token)
        is_temp = True

    if not target_data:
        raise HTTPException(status_code=404, detail="Invalid or expired link")

    if store.is_banned(target_id):
        raise HTTPException(status_code=404, detail="Invalid or expired link")

    # Increment temp link usage
    if is_temp:
        await store.use_temp_link(body.token)

    # Store in webapp_messages
    await store.store_webapp_message(
        sender_id=0,  # anonymous web sender
        receiver_id=target_id,
        sender_nickname="Web visitor",
        message_text=body.text.strip(),
        message_type="text",
    )

    # Send via bot to recipient's Telegram chat
    try:
        client = get_client()
        webapp_msg_text = (await gstr("webapp_new_message", user_id=target_id))
        caption = f"<blockquote>{body.text.strip()}</blockquote>\n\n{webapp_msg_text}"
        await client.send_message(
            target_id,
            caption,
            parse_mode="html",
        )
    except Exception as e:
        logger.warning(f"Failed to send webapp message to {target_id}: {e}")
        # Still return ok — message is saved in inbox

    return {"ok": True}


@app.get("/api/inbox")
def get_inbox(request: Request):
    """Get user's inbox messages (requires initData)."""
    user = get_user_from_init_data(request)
    user_id = user["id"]
    store = get_store()

    offset = int(request.query_params.get("offset", 0))
    limit = int(request.query_params.get("limit", 50))
    limit = min(limit, 100)

    messages = store.get_inbox_messages(user_id, limit=limit, offset=offset)
    unread_count = store.get_unread_count(user_id)

    return {"messages": messages, "unread_count": unread_count}


@app.post("/api/inbox/read/{message_id}")
async def mark_read(message_id: int, request: Request):
    """Mark a message as read."""
    user = get_user_from_init_data(request)
    user_id = user["id"]
    store = get_store()

    success = await store.mark_message_read(message_id, user_id)
    if not success:
        raise HTTPException(status_code=404, detail="Message not found")

    return {"ok": True}


@app.get("/api/debug/validate")
def debug_validate(request: Request):
    """Debug endpoint: check if initData is valid without failing."""
    init_data = request.headers.get("X-Init-Data", "")
    if not init_data:
        return {"valid": False, "reason": "no initData header", "length": 0}

    parsed = parse_qs(init_data, keep_blank_values=True)
    has_hash = "hash" in parsed
    has_user = "user" in parsed
    user = validate_init_data(init_data)

    return {
        "valid": user is not None,
        "length": len(init_data),
        "has_hash": has_hash,
        "has_user": has_user,
        "keys": list(parsed.keys()),
        "user_id": user.get("id") if user else None,
    }
