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

try:
    from pilmoji import Pilmoji
    _HAS_PILMOJI = True
except ImportError:
    _HAS_PILMOJI = False
from pyrogram.enums import ParseMode

from .config import config
from .store import get_store
from .client import get_client
from .strings import gstr

logger = logging.getLogger(__name__)

_bot_username: str = ""
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WEB_ROOT = "/var/www/html"
_AVATARS_DIR = os.path.join(_WEB_ROOT, "avatars") if os.path.isdir(_WEB_ROOT) else os.path.join(_PROJECT_ROOT, "avatars")

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

    # Auto-generate avatar if missing
    nickname = stats.get("nickname", "")
    avatar_path = os.path.join(_AVATARS_DIR, f"{user_id}.png")
    if nickname and not os.path.isfile(avatar_path):
        _generate_avatar(user_id, nickname)
    if os.path.isfile(avatar_path):
        stats["avatar_url"] = f"/avatars/{user_id}.png?t={int(os.path.getmtime(avatar_path))}"
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

    img = _render_story_card(nickname, reg_text, user_id=target_id)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=300"},
    )


# Story card assets
_ASSETS_DIRS = [os.path.join(_WEB_ROOT, "assets"), os.path.join(_PROJECT_ROOT, "assets")]
_FRAME_PATH = os.path.join(_WEB_ROOT, "addons", "frame.webp")

# Same gradient palette as the frontend dashboard (tied to nickname hash)
_GRADIENTS = [
    ("#FF6B6B", "#EE5A24"), ("#A29BFE", "#6C5CE7"), ("#55E6C1", "#1ABC9C"),
    ("#FECA57", "#FF9F43"), ("#FF9FF3", "#F368E0"), ("#48DBFB", "#0ABDE3"),
    ("#FF6348", "#FF4757"), ("#7BED9F", "#2ED573"), ("#70A1FF", "#1E90FF"),
    ("#FFA502", "#E17055"), ("#DCDDE1", "#A4B0BD"), ("#FD79A8", "#E84393"),
    ("#BADC58", "#6AB04C"), ("#F8C291", "#E55039"), ("#82CCDD", "#3C6382"),
    ("#B8E994", "#78E08F"), ("#FDA7DF", "#D980FA"), ("#F7D794", "#F5CD79"),
]


def _find_asset(filename: str) -> str:
    for d in _ASSETS_DIRS:
        p = os.path.join(d, filename)
        if os.path.isfile(p):
            return p
    return os.path.join(_ASSETS_DIRS[-1], filename)


_STORY_BG_PATH = _find_asset("story-bg.png")
_SATISFY_PATH = _find_asset("satisfy.ttf")


def _nick_hash(nickname: str) -> int:
    """Same hash as frontend: h = (h * 31 + charCode) | 0, then abs.

    The | 0 forces 32-bit signed integer truncation (matching JS behavior).
    """
    h = 0
    for c in nickname:
        h = (h * 31 + ord(c)) & 0xFFFFFFFF
        if h >= 0x80000000:
            h -= 0x100000000
    return abs(h)


def _hex_to_rgb(hex_color: str) -> tuple:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


def _load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except (OSError, IOError):
        return ImageFont.load_default()


def _fit_text_font(path: str, text: str, max_w: int, start_size: int = 200, min_size: int = 48):
    """Find the largest font size so that `text` fits within `max_w`."""
    for size in range(start_size, min_size - 1, -4):
        font = _load_font(path, size)
        bbox = font.getbbox(text)
        if bbox[2] - bbox[0] <= max_w:
            return font, bbox
    font = _load_font(path, min_size)
    return font, font.getbbox(text)


def _draw_gradient_circle(size: int, color1: tuple, color2: tuple) -> Image.Image:
    """Draw a circular avatar with a 135-degree linear gradient."""
    # Build oversized gradient then rotate+crop for clean diagonal
    big = int(size * 1.5)
    grad = Image.new("RGB", (big, big), color1)
    draw_g = ImageDraw.Draw(grad)
    for y in range(big):
        t = y / big
        r = int(color1[0] + (color2[0] - color1[0]) * t)
        g = int(color1[1] + (color2[1] - color1[1]) * t)
        b = int(color1[2] + (color2[2] - color1[2]) * t)
        draw_g.line([(0, y), (big, y)], fill=(r, g, b))
    grad = grad.rotate(45, resample=Image.BICUBIC, expand=False, fillcolor=color2)
    # Center-crop to target size
    left = (big - size) // 2
    top = (big - size) // 2
    grad = grad.crop((left, top, left + size, top + size)).convert("RGBA")
    # Apply circular mask
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size - 1, size - 1], fill=255)
    grad.putalpha(mask)
    return grad


# Animals and face expressions only
_AVATAR_EMOJIS = [
    # Animals
    "\U0001F98A", "\U0001F43C", "\U0001F98B", "\U0001F42C", "\U0001F984",
    "\U0001F427", "\U0001F981", "\U0001F438", "\U0001F989", "\U0001F43A",
    "\U0001F988", "\U0001F419", "\U0001F99C", "\U0001F439", "\U0001F99D",
    "\U0001F42F", "\U0001F428", "\U0001F9A9", "\U0001F43B", "\U0001F430",
    "\U0001F980", "\U0001F41D", "\U0001F433", "\U0001F98E", "\U0001F43F\uFE0F",
    "\U0001F987", "\U0001F42E", "\U0001F414", "\U0001F432",
    # Face expressions
    "\U0001F60E", "\U0001F913", "\U0001F978", "\U0001F929", "\U0001F47B",
    "\U0001F608", "\U0001F92F", "\U0001F974", "\U0001F60D", "\U0001F643",
    "\U0001F972", "\U0001F970", "\U0001F60B", "\U0001F917", "\U0001F92D",
]


def _render_avatar(nickname: str, size: int = 400) -> Image.Image:
    """Render avatar with gradient circle + emoji + frame overlay.

    Returns an RGBA image of the given size (frame included).
    """
    h = _nick_hash(nickname)
    grad = _GRADIENTS[h % len(_GRADIENTS)]
    c1, c2 = _hex_to_rgb(grad[0]), _hex_to_rgb(grad[1])
    emoji = _AVATAR_EMOJIS[h % len(_AVATAR_EMOJIS)]

    # Circle is 80% of total size; frame fills full size
    circle_size = int(size * 0.80)
    circle_img = _draw_gradient_circle(circle_size, c1, c2)

    # Render emoji (or letter fallback)
    emoji_layer = Image.new("RGBA", (circle_size, circle_size), (0, 0, 0, 0))
    emoji_font_size = int(circle_size * 0.38)
    if _HAS_PILMOJI:
        emoji_font = _load_font(_SATISFY_PATH, emoji_font_size)
        with Pilmoji(emoji_layer) as pmoji:
            ew, eh = pmoji.getsize(emoji, font=emoji_font)
            ex = (circle_size - ew) // 2
            ey = (circle_size - eh) // 2 - int(circle_size * 0.06)
            pmoji.text((ex, ey), emoji, font=emoji_font)
    else:
        logger.warning("pilmoji not installed — falling back to letter avatar")
        letter_font = _load_font(_SATISFY_PATH, int(circle_size * 0.46))
        letter = nickname[0].upper() if nickname else "?"
        ld = ImageDraw.Draw(emoji_layer)
        bbox = ld.textbbox((0, 0), letter, font=letter_font)
        lw, lh = bbox[2] - bbox[0], bbox[3] - bbox[1]
        ld.text(((circle_size - lw) // 2 - bbox[0], (circle_size - lh) // 2 - bbox[1]),
                letter, fill=(255, 255, 255, 255), font=letter_font)
    circle_img = Image.alpha_composite(circle_img, emoji_layer)

    # Compose onto full-size canvas
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    offset = (size - circle_size) // 2
    canvas.paste(circle_img, (offset, offset), circle_img)

    # Frame overlay
    try:
        frame = Image.open(_FRAME_PATH).convert("RGBA")
        frame = frame.resize((size, size), Image.LANCZOS)
        canvas = Image.alpha_composite(canvas, frame)
    except (OSError, IOError):
        pass

    return canvas


def _generate_avatar(user_id: int, nickname: str) -> str:
    """Generate avatar and save to avatars/{user_id}.png. Returns the file path."""
    os.makedirs(_AVATARS_DIR, exist_ok=True)
    avatar = _render_avatar(nickname, size=400)
    path = os.path.join(_AVATARS_DIR, f"{user_id}.png")
    avatar.save(path, "PNG", optimize=True)
    logger.info("Generated avatar for user %s at %s", user_id, path)
    return path


def delete_avatar_file(user_id: int) -> None:
    """Delete the avatar file for a user (called on revoke)."""
    path = os.path.join(_AVATARS_DIR, f"{user_id}.png")
    try:
        os.remove(path)
    except OSError:
        pass


def _render_story_card(nickname: str, reg_text: str, user_id: int = None) -> Image.Image:
    """Render a 1080x1920 story card with avatar, frame, nickname, reg time."""
    W, H = 1080, 1920

    # Load background
    try:
        img = Image.open(_STORY_BG_PATH).convert("RGBA")
        img = img.resize((W, H), Image.LANCZOS)
    except (OSError, IOError):
        img = Image.new("RGBA", (W, H))
        d = ImageDraw.Draw(img)
        for y in range(H):
            t = y / H
            d.line([(0, y), (W, y)], fill=(int(15 + 33 * t), int(12 + 31 * t), int(41 + 58 * t), 255))

    cx = W // 2

    # --- Avatar (load pre-rendered PNG from disk, or render fresh) ---
    avatar_display_size = 320
    avatar_img = None
    if user_id:
        avatar_path = os.path.join(_AVATARS_DIR, f"{user_id}.png")
        if os.path.isfile(avatar_path):
            try:
                saved = Image.open(avatar_path).convert("RGBA")
                avatar_img = saved.resize((avatar_display_size, avatar_display_size), Image.LANCZOS)
            except (OSError, IOError):
                pass
    if avatar_img is None:
        avatar_img = _render_avatar(nickname, size=avatar_display_size)

    avatar_y = 520
    img.paste(avatar_img, (cx - avatar_display_size // 2, avatar_y), avatar_img)

    draw = ImageDraw.Draw(img)

    # --- Nickname (auto-sized, below avatar) ---
    pad = 120
    text_top = avatar_y + avatar_display_size + 80
    nick_font, nick_bbox = _fit_text_font(_SATISFY_PATH, nickname, W - pad * 2, 160, 56)
    nw = nick_bbox[2] - nick_bbox[0]
    nh = nick_bbox[3] - nick_bbox[1]
    draw.text((cx - nw // 2, text_top), nickname, fill=(255, 255, 255, 255), font=nick_font)

    # --- Registration text (below nickname) ---
    if reg_text:
        reg_font = _load_font(_SATISFY_PATH, 44)
        bbox = reg_font.getbbox(reg_text)
        rw = bbox[2] - bbox[0]
        draw.text((cx - rw // 2, text_top + nh + 30), reg_text, fill=(255, 255, 255, 170), font=reg_font)

    # --- Bottom branding ---
    brand_font = _load_font(_SATISFY_PATH, 36)
    bbox = brand_font.getbbox("Incognitus")
    bw = bbox[2] - bbox[0]
    draw.text((cx - bw // 2, H - 160), "Incognitus", fill=(255, 255, 255, 90), font=brand_font)

    return img.convert("RGB")
