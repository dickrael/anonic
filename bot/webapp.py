"""FastAPI webapp server for Telegram Mini App integration."""

import hashlib
import hmac
import io
import json
import logging
import os
from urllib.parse import parse_qs, unquote

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel
from PIL import Image, ImageDraw, ImageFont
from pyrogram.enums import ParseMode

from .config import config
from .store import get_store
from .client import get_client
from .strings import gstr

logger = logging.getLogger(__name__)

_bot_username: str = ""
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = FastAPI(title="Incognitus WebApp API", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


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


@app.get("/api/story-card/{token}")
async def story_card(token: str):
    """Generate a styled 1080x1920 story card image for sharing."""
    store = get_store()
    target_id, target_data = store.get_by_token(token)
    if not target_data:
        raise HTTPException(status_code=404, detail="Not found")

    nickname = target_data.get("nickname", "???")

    # Compute registration relative text
    from datetime import datetime, timezone
    reg_str = target_data.get("registered_at", "")
    reg_text = ""
    if reg_str:
        try:
            reg_dt = datetime.fromisoformat(reg_str.replace("Z", "+00:00"))
            diff = datetime.now(timezone.utc) - reg_dt
            total_min = int(diff.total_seconds() / 60)
            if total_min < 1:
                reg_text = "Registered just now"
            elif total_min < 60:
                reg_text = f"Registered {total_min}m ago"
            elif total_min < 1440:
                reg_text = f"Registered {total_min // 60}h ago"
            else:
                reg_text = f"Registered {diff.days} days ago"
        except Exception:
            pass

    img = _render_story_card(nickname, reg_text)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=300"},
    )


# Story card assets
_ASSETS_DIR = os.path.join(_PROJECT_ROOT, "assets")
_STORY_BG_PATH = os.path.join(_ASSETS_DIR, "story-bg.png")
_SATISFY_PATH = os.path.join(_ASSETS_DIR, "satisfy.ttf")


def _load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    """Load a TrueType font or fall back to Pillow default."""
    try:
        return ImageFont.truetype(path, size)
    except (OSError, IOError):
        return ImageFont.load_default()


def _fit_text_font(path: str, text: str, max_w: int, start_size: int = 200, min_size: int = 48):
    """Find the largest font size so that `text` fits within `max_w` pixels."""
    for size in range(start_size, min_size - 1, -4):
        font = _load_font(path, size)
        bbox = font.getbbox(text)
        if bbox[2] - bbox[0] <= max_w:
            return font, bbox
    font = _load_font(path, min_size)
    return font, font.getbbox(text)


def _render_story_card(nickname: str, reg_text: str) -> Image.Image:
    """Render a 1080x1920 story card: bg + auto-sized nickname + registration."""
    W, H = 1080, 1920

    # Load background image or fallback to gradient
    try:
        img = Image.open(_STORY_BG_PATH).convert("RGBA")
        img = img.resize((W, H), Image.LANCZOS)
    except (OSError, IOError):
        img = Image.new("RGBA", (W, H))
        draw = ImageDraw.Draw(img)
        for y in range(H):
            t = y / H
            r, g, b = int(15 + 33 * t), int(12 + 31 * t), int(41 + 58 * t)
            draw.line([(0, y), (W, y)], fill=(r, g, b, 255))

    cx = W // 2
    pad = 120  # horizontal padding

    # Semi-transparent dark overlay for readability
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle(
        [60, H // 2 - 220, W - 60, H // 2 + 220],
        radius=40,
        fill=(0, 0, 0, 80),
    )
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)

    # --- Nickname (auto-sized to fill width, decorative font) ---
    nick_font, nick_bbox = _fit_text_font(_SATISFY_PATH, nickname, W - pad * 2, 200, 60)
    nw = nick_bbox[2] - nick_bbox[0]
    nh = nick_bbox[3] - nick_bbox[1]
    nick_y = H // 2 - nh // 2 - 40
    draw.text((cx - nw // 2, nick_y), nickname, fill=(255, 255, 255, 255), font=nick_font)

    # --- Registration time (below nickname) ---
    if reg_text:
        reg_font = _load_font(_SATISFY_PATH, 44)
        bbox = reg_font.getbbox(reg_text)
        rw = bbox[2] - bbox[0]
        reg_y = nick_y + nh + 35
        draw.text((cx - rw // 2, reg_y), reg_text, fill=(255, 255, 255, 170), font=reg_font)

    # --- Bottom branding ---
    brand_font = _load_font(_SATISFY_PATH, 36)
    bbox = brand_font.getbbox("Incognitus")
    bw = bbox[2] - bbox[0]
    draw.text((cx - bw // 2, H - 160), "Incognitus", fill=(255, 255, 255, 90), font=brand_font)

    return img.convert("RGB")
