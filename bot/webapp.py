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

    # Compute member-since relative text
    from datetime import datetime, timezone
    reg_str = target_data.get("registered_at", "")
    member_since = ""
    if reg_str:
        try:
            reg_dt = datetime.fromisoformat(reg_str.replace("Z", "+00:00"))
            diff = datetime.now(timezone.utc) - reg_dt
            total_min = int(diff.total_seconds() / 60)
            if total_min < 1:
                member_since = "Just joined"
            elif total_min < 60:
                member_since = f"Member for {total_min}m"
            elif total_min < 1440:
                member_since = f"Member for {total_min // 60}h"
            else:
                member_since = f"Member for {diff.days}d"
        except Exception:
            pass

    img = _render_story_card(nickname, member_since)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=300"},
    )


# Pre-load story background and fonts at startup
_ASSETS_DIR = os.path.join(_PROJECT_ROOT, "assets")
_FONT_DIR = os.path.join(_ASSETS_DIR, "fonts")
_STORY_BG_PATH = os.path.join(_ASSETS_DIR, "story-bg.png")


def _load_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    """Try to load a font from assets/fonts, fallback to default."""
    try:
        return ImageFont.truetype(os.path.join(_FONT_DIR, name), size)
    except (OSError, IOError):
        return ImageFont.load_default()


def _render_story_card(nickname: str, member_since: str) -> Image.Image:
    """Render a 1080x1920 story card: bg image + nickname + member since."""
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
            r = int(15 + (48 - 15) * t)
            g = int(12 + (43 - 12) * t)
            b = int(41 + (99 - 41) * t)
            draw.line([(0, y), (W, y)], fill=(r, g, b, 255))

    # Fonts — decorative for nickname, clean for subtitle
    font_nick = _load_font("Decorative.ttf", 96)
    font_since = _load_font("Inter-SemiBold.ttf", 44)
    font_brand = _load_font("Inter-Medium.ttf", 36)

    cx = W // 2

    # Semi-transparent dark overlay in center area for text readability
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle(
        [80, H // 2 - 200, W - 80, H // 2 + 200],
        radius=40,
        fill=(0, 0, 0, 90),
    )
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)

    # --- Nickname (big, centered) ---
    bbox = draw.textbbox((0, 0), nickname, font=font_nick)
    nw, nh = bbox[2] - bbox[0], bbox[3] - bbox[1]
    nick_y = H // 2 - nh // 2 - 30
    draw.text((cx - nw // 2, nick_y), nickname, fill=(255, 255, 255, 255), font=font_nick)

    # --- Member since (below nickname) ---
    if member_since:
        bbox = draw.textbbox((0, 0), member_since, font=font_since)
        sw = bbox[2] - bbox[0]
        since_y = nick_y + nh + 30
        draw.text((cx - sw // 2, since_y), member_since, fill=(255, 255, 255, 180), font=font_since)

    # --- Bottom branding ---
    brand = "Incognitus"
    bbox = draw.textbbox((0, 0), brand, font=font_brand)
    bw = bbox[2] - bbox[0]
    draw.text((cx - bw // 2, H - 160), brand, fill=(255, 255, 255, 100), font=font_brand)

    return img.convert("RGB")
