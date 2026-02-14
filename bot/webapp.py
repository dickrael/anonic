"""FastAPI webapp server for Telegram Mini App integration."""

import hashlib
import hmac
import io
import json
import logging
import os
import time
from urllib.parse import parse_qs, unquote

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from PIL import Image
from pyrogram.enums import ParseMode

from .config import config
from .store import get_store
from .client import get_client
from .strings import gstr

logger = logging.getLogger(__name__)

_bot_username: str = ""

app = FastAPI(title="Incognitus WebApp API", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Avatars directory next to the project root
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AVATARS_DIR = os.path.join(_PROJECT_ROOT, "avatars")
os.makedirs(AVATARS_DIR, exist_ok=True)


def validate_init_data(init_data: str) -> dict | None:
    """Validate Telegram WebApp initData using HMAC-SHA256."""
    try:
        parsed = parse_qs(init_data, keep_blank_values=True)
        received_hash = parsed.get("hash", [None])[0]
        if not received_hash:
            return None

        pairs = []
        for key, values in parsed.items():
            if key == "hash":
                continue
            pairs.append(f"{key}={values[0]}")
        pairs.sort()
        data_check_string = "\n".join(pairs)

        secret_key = hmac.new(
            b"WebAppData", config.bot_token.encode(), hashlib.sha256
        ).digest()
        computed_hash = hmac.new(
            secret_key, data_check_string.encode(), hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(computed_hash, received_hash):
            return None

        user_json = parsed.get("user", [None])[0]
        if not user_json:
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
    user = validate_init_data(init_data)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid initData")
    return user


# ---- Models ----

class SendMessageRequest(BaseModel):
    token: str
    text: str


# ---- Endpoints ----

@app.get("/api/health")
async def health():
    return {"status": "ok", "port": config.webapp_port}


@app.get("/api/link/{token}")
async def get_link_info(token: str):
    """Get recipient nickname for share page."""
    store = get_store()
    target_id, target_data = store.get_by_token(token)
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

    target_id, target_data = store.get_by_token(body.token)
    is_temp = False
    if not target_data:
        target_id, target_data = store.get_user_by_temp_link(body.token)
        is_temp = True

    if not target_data:
        raise HTTPException(status_code=404, detail="Invalid or expired link")
    if store.is_banned(target_id):
        raise HTTPException(status_code=404, detail="Invalid or expired link")

    if is_temp:
        await store.use_temp_link(body.token)

    await store.store_webapp_message(
        sender_id=0,
        receiver_id=target_id,
        sender_nickname="Web visitor",
        message_text=body.text.strip(),
        message_type="text",
    )

    try:
        client = get_client()
        webapp_msg_text = (await gstr("webapp_new_message", user_id=target_id))
        caption = f"<blockquote>{body.text.strip()}</blockquote>\n\n{webapp_msg_text}"
        await client.send_message(target_id, caption, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.warning(f"Failed to send webapp message to {target_id}: {e}")

    return {"ok": True}


@app.get("/api/dashboard")
async def get_dashboard(request: Request):
    """Get user's dashboard statistics (requires initData)."""
    user = get_user_from_init_data(request)
    user_id = user["id"]
    store = get_store()
    stats = store.get_dashboard_stats(user_id)
    if not stats:
        raise HTTPException(status_code=404, detail="User not found")
    global _bot_username
    if not _bot_username:
        client = get_client()
        _bot_username = client.me.username if client.me else "ClearSayBot"
    stats["bot_username"] = _bot_username
    return stats


@app.get("/avatars/{filename}")
async def get_avatar(filename: str):
    """Serve avatar image files."""
    # Sanitize: only allow {digits}.jpg
    if not filename.endswith(".jpg") or not filename[:-4].isdigit():
        raise HTTPException(status_code=404, detail="Not found")
    filepath = os.path.join(AVATARS_DIR, filename)
    if not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(filepath, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=3600"})


MAX_AVATAR_SIZE = 5 * 1024 * 1024  # 5 MB


@app.post("/api/avatar")
async def upload_avatar(request: Request, file: UploadFile = File(...)):
    """Upload or replace user profile photo."""
    user = get_user_from_init_data(request)
    user_id = user["id"]

    # Read and validate size
    data = await file.read()
    if len(data) > MAX_AVATAR_SIZE:
        raise HTTPException(status_code=400, detail="File too large (max 5 MB)")

    # Validate image and process with Pillow
    try:
        img = Image.open(io.BytesIO(data))
        img = img.convert("RGB")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid image file")

    # Center-crop to square
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    img = img.crop((left, top, left + side, top + side))

    # Resize to 200x200
    img = img.resize((200, 200), Image.LANCZOS)

    # Save as JPEG
    avatar_path = os.path.join(AVATARS_DIR, f"{user_id}.jpg")
    img.save(avatar_path, "JPEG", quality=85)

    # Update DB
    store = get_store()
    relative_path = f"avatars/{user_id}.jpg"
    await store.set_avatar(user_id, relative_path)

    return {
        "ok": True,
        "avatar_url": f"/avatars/{user_id}.jpg?t={int(time.time())}",
    }


@app.delete("/api/avatar")
async def delete_avatar(request: Request):
    """Delete user profile photo."""
    user = get_user_from_init_data(request)
    user_id = user["id"]

    # Delete file from disk
    avatar_path = os.path.join(AVATARS_DIR, f"{user_id}.jpg")
    if os.path.exists(avatar_path):
        os.remove(avatar_path)

    # Clear DB
    store = get_store()
    await store.delete_avatar(user_id)

    return {"ok": True}
