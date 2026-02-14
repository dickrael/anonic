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

    global _bot_username
    if not _bot_username:
        client = get_client()
        _bot_username = client.me.username if client.me else "ClearSayBot"

    link = f"t.me/{_bot_username}?start={token}"

    img = _render_story_card(nickname, link)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=300"},
    )


def _render_story_card(nickname: str, link: str) -> Image.Image:
    """Render a 1080x1920 story card with gradient background and text."""
    W, H = 1080, 1920

    # Work in RGBA for proper alpha compositing
    img = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    draw = ImageDraw.Draw(img)

    # Gradient background: deep purple → dark blue
    for y in range(H):
        t = y / H
        r = int(15 + (48 - 15) * t)
        g = int(12 + (43 - 12) * t)
        b = int(41 + (99 - 41) * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b, 255))

    # Soft decorative glow blobs
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([(-100, -100), (500, 500)], fill=(138, 43, 226, 25))
    gd.ellipse([(600, 1300), (1200, 1900)], fill=(64, 167, 227, 25))
    gd.ellipse([(300, 700), (800, 1200)], fill=(168, 85, 247, 18))
    img = Image.alpha_composite(img, glow)
    draw = ImageDraw.Draw(img)

    # Load fonts — bundled Inter or fallback to default
    font_dir = os.path.join(_PROJECT_ROOT, "assets", "fonts")
    try:
        font_big = ImageFont.truetype(os.path.join(font_dir, "Inter-Bold.ttf"), 72)
        font_mid = ImageFont.truetype(os.path.join(font_dir, "Inter-SemiBold.ttf"), 48)
        font_small = ImageFont.truetype(os.path.join(font_dir, "Inter-Medium.ttf"), 38)
        font_link = ImageFont.truetype(os.path.join(font_dir, "Inter-Bold.ttf"), 42)
    except (OSError, IOError):
        font_big = ImageFont.load_default()
        font_mid = font_big
        font_small = font_big
        font_link = font_big

    cx = W // 2

    # --- Avatar circle with glow ---
    circle_r = 120
    circle_y = 580
    # Outer glow ring
    glow_ring = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    grd = ImageDraw.Draw(glow_ring)
    for i in range(20, 0, -1):
        a = int(25 * (1 - i / 20))
        rr = circle_r + i
        grd.ellipse([cx - rr, circle_y - rr, cx + rr, circle_y + rr], fill=(138, 43, 226, a))
    img = Image.alpha_composite(img, glow_ring)
    draw = ImageDraw.Draw(img)
    # Solid circle
    draw.ellipse(
        [cx - circle_r, circle_y - circle_r, cx + circle_r, circle_y + circle_r],
        fill=(88, 55, 180, 255),
    )

    # First letter of nickname in circle (reliable cross-platform)
    letter = nickname[0].upper() if nickname else "?"
    bbox = draw.textbbox((0, 0), letter, font=font_big)
    lw, lh = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((cx - lw // 2, circle_y - lh // 2), letter, fill=(255, 255, 255, 255), font=font_big)

    # --- Nickname ---
    nick_y = circle_y + circle_r + 50
    bbox = draw.textbbox((0, 0), nickname, font=font_big)
    nw = bbox[2] - bbox[0]
    draw.text((cx - nw // 2, nick_y), nickname, fill=(255, 255, 255, 255), font=font_big)

    # --- Tagline ---
    tagline = "Send me an anonymous message!"
    tag_y = nick_y + 100
    bbox = draw.textbbox((0, 0), tagline, font=font_mid)
    tw = bbox[2] - bbox[0]
    draw.text((cx - tw // 2, tag_y), tagline, fill=(200, 200, 230, 255), font=font_mid)

    # --- Pill-shaped link box ---
    pill_y = tag_y + 120
    pill_h = 80
    pill_pad = 60
    bbox = draw.textbbox((0, 0), link, font=font_link)
    link_w = bbox[2] - bbox[0]
    link_h = bbox[3] - bbox[1]
    pill_w = link_w + pill_pad * 2
    pill_x = cx - pill_w // 2
    pill_r = pill_h // 2
    # Draw on a separate layer for translucent fill
    pill_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    pd = ImageDraw.Draw(pill_layer)
    pd.rounded_rectangle(
        [pill_x, pill_y, pill_x + pill_w, pill_y + pill_h],
        radius=pill_r,
        fill=(255, 255, 255, 30),
        outline=(255, 255, 255, 80),
        width=2,
    )
    img = Image.alpha_composite(img, pill_layer)
    draw = ImageDraw.Draw(img)
    draw.text(
        (cx - link_w // 2, pill_y + (pill_h - link_h) // 2),
        link,
        fill=(255, 255, 255, 255),
        font=font_link,
    )

    # --- Bottom branding ---
    brand = "Incognitus"
    brand_y = H - 180
    bbox = draw.textbbox((0, 0), brand, font=font_small)
    bw = bbox[2] - bbox[0]
    draw.text((cx - bw // 2, brand_y), brand, fill=(255, 255, 255, 120), font=font_small)

    return img.convert("RGB")
