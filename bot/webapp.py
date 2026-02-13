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


class ReplyMessageRequest(BaseModel):
    message_id: int
    text: str


# ---- Endpoints ----

@app.get("/api/health")
async def health():
    """Health check."""
    return {"status": "ok", "port": config.webapp_port}


@app.get("/api/link/{token}")
async def get_link_info(token: str):
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
async def get_inbox(request: Request):
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


@app.get("/api/inbox/stats")
async def get_inbox_stats(request: Request):
    """Get user's inbox statistics for dashboard."""
    user = get_user_from_init_data(request)
    user_id = user["id"]
    store = get_store()
    stats = store.get_inbox_stats(user_id)
    return stats


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


@app.post("/api/inbox/reply")
async def reply_to_message(body: ReplyMessageRequest, request: Request):
    """Reply to an inbox message from the webapp."""
    user = get_user_from_init_data(request)
    user_id = user["id"]
    store = get_store()

    if not body.text or not body.text.strip():
        raise HTTPException(status_code=400, detail="Reply cannot be empty")
    if len(body.text) > 4096:
        raise HTTPException(status_code=400, detail="Reply too long (max 4096)")

    # Find the original message to get sender_id
    messages = store.get_inbox_messages(user_id, limit=1000, offset=0)
    target_msg = None
    for msg in messages:
        if msg["id"] == body.message_id:
            target_msg = msg
            break

    if not target_msg:
        raise HTTPException(status_code=404, detail="Message not found")

    sender_id = target_msg["sender_id"]
    if sender_id == 0:
        raise HTTPException(status_code=400, detail="Cannot reply to anonymous web visitors")

    sender = store.get_user(sender_id)
    if not sender:
        raise HTTPException(status_code=404, detail="Sender no longer exists")

    replier = store.get_user(user_id)
    replier_nickname = replier["nickname"] if replier else "User"

    # Send reply via bot
    try:
        client = get_client()
        from .handlers.messaging import _sparkle_row
        sparkle = _sparkle_row()
        caption = (await gstr("anonymous_caption", user_id=sender_id)).format(
            original=body.text.strip(),
            nickname=replier_nickname,
            sparkle_row=sparkle,
        )
        caption += "\n" + (await gstr("anonymous_reply_instruction", user_id=sender_id))
        sent = await client.send_message(sender_id, caption, parse_mode="html")
        # Store for reply routing
        await store.store_message(sent.id, user_id, sender_id)
    except Exception as e:
        logger.warning(f"Failed to send reply to {sender_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to deliver reply")

    # Also store in webapp inbox of the sender
    await store.store_webapp_message(
        sender_id=user_id,
        receiver_id=sender_id,
        sender_nickname=replier_nickname,
        message_text=body.text.strip(),
        message_type="text",
    )

    # Update stats
    await store.increment_messages_sent(user_id)
    await store.increment_messages_received(sender_id)

    return {"ok": True}


@app.get("/api/debug/validate")
async def debug_validate(request: Request):
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
