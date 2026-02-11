"""Lock/unlock message type handlers with inline keyboard UI."""

import asyncio
import logging
from typing import Dict, List

from pyrogram import Client, filters
from pyrogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
)
from pyrogram.enums import ParseMode, ButtonStyle
from pyrogram.errors import MessageNotModified

from ..store import get_store
from ..strings import gstr

logger = logging.getLogger(__name__)

# Items per page for pagination
ITEMS_PER_PAGE = 8

# Track auto-delete tasks by message_id to allow resetting on interaction
_auto_delete_tasks: Dict[int, asyncio.Task] = {}

# Type descriptions for info buttons
# Custom emoji IDs for each type (icon_custom_emoji_id on buttons)
TYPE_EMOJI_ID: Dict[str, int] = {
    "url": 5877465816030515018,
    "email": 5967280668885913944,
    "phone": 5897938112654348733,
    "cashtag": 5974217466270716579,
    "hashtag": 5807558162361884395,
    "spoiler": 6008135256798927387,
    "emoji": 5942913498349571809,
    "emojionly": 5933613451044720529,
    "emojicustom": 5942640218170461901,
    "cyrillic": 6007963990683030781,
    "zalgo": 6005930547006674040,
    "photo": 6021321527952153006,
    "video": 6005986106703613755,
    "gif": 5945068566909815651,
    "voice": 5897554554894946515,
    "videonote": 5891119667388354506,
    "audio": 5891249688933305846,
    "document": 5877495434124988415,
    "sticker": 5784982040432611567,
    "stickeranimated": 5814652112600043672,
    "location": 5944940516754853337,
    "poll": 5875291072225087249,
    "game": 6023852878597200124,
    "emojigame": 6021391505854306270,
    "forward": 5832251986635920010,
    "forwardbot": 5832251986635920010,
    "forwardchannel": 5832251986635920010,
    "forwarduser": 5832251986635920010,
    "forwardstory": 5832251986635920010,
    "externalreply": 5888484185261216745,
}

TYPE_INFO: Dict[str, str] = {
    "all": "Toggle all message types at once",
    # Content
    "text": "Plain text messages (always allowed)",
    "url": "Messages containing URLs/links",
    "email": "Messages containing email addresses",
    "phone": "Messages containing phone numbers",
    "cashtag": "Cash tags like $USD, $BTC",
    "hashtag": "Messages containing #hashtags",
    "spoiler": "Messages with spoiler formatting",
    # Text filters
    "emoji": "Messages containing any emoji",
    "emojionly": "Messages that are ONLY emojis",
    "emojicustom": "Custom/premium emoji",
    "cyrillic": "Messages in Cyrillic script",
    "zalgo": "Glitch text with excessive formatting characters",
    # Media
    "photo": "Photo/image messages",
    "video": "Video messages",
    "gif": "GIF animations",
    "voice": "Voice messages",
    "videonote": "Video notes (round videos)",
    "audio": "Audio/music files",
    "document": "Documents/files",
    # Stickers
    "sticker": "Regular stickers",
    "stickeranimated": "Animated stickers",
    # Interactive
    "location": "Location sharing",
    "poll": "Polls",
    "game": "Games shared via inline bots",
    "emojigame": "Dice, bowling, darts mini-games",
    # Forwards
    "forward": "Any forwarded message",
    "forwardbot": "Messages forwarded from bots",
    "forwardchannel": "Messages forwarded from channels",
    "forwardstory": "Forwarded stories",
    "forwarduser": "Messages forwarded from users",
    # Other
    "externalreply": "Quote-reply messages",
}

# Categorized types for organized display
TYPE_CATEGORIES = [
    ("📝", ["url", "email", "phone", "cashtag", "hashtag", "spoiler"]),
    ("🔤", ["emoji", "emojionly", "emojicustom", "cyrillic", "zalgo"]),
    ("📷", ["photo", "video", "gif", "voice", "videonote", "audio", "document"]),
    ("🎭", ["sticker", "stickeranimated"]),
    ("🎮", ["location", "poll", "game", "emojigame"]),
    ("↩️", ["forward", "forwardbot", "forwardchannel", "forwardstory", "forwarduser"]),
    ("📎", ["externalreply"]),
]


def get_all_types() -> List[str]:
    """Get flat list of all lockable types in order."""
    types = []
    for _, category_types in TYPE_CATEGORIES:
        types.extend(category_types)
    return types


def build_locktypes_keyboard(user_id: int, page: int = 0) -> InlineKeyboardMarkup:
    """Build inline keyboard for lock types with pagination."""
    store = get_store()
    allowed_types = store.get_allowed_types(str(user_id))
    all_types = get_all_types()

    total_pages = (len(all_types) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    page = max(0, min(page, total_pages - 1))

    start_idx = page * ITEMS_PER_PAGE
    end_idx = min(start_idx + ITEMS_PER_PAGE, len(all_types))
    page_types = all_types[start_idx:end_idx]

    buttons = []

    for msg_type in page_types:
        is_allowed = msg_type in allowed_types
        status = "✅" if is_allowed else "🚫"
        toggle_style = ButtonStyle.SUCCESS if is_allowed else ButtonStyle.DANGER

        emoji_id = TYPE_EMOJI_ID.get(msg_type)
        name_btn_kwargs = {}
        if emoji_id:
            name_btn_kwargs["icon_custom_emoji_id"] = emoji_id

        buttons.append([
            InlineKeyboardButton(
                msg_type,
                callback_data=f"lt:i:{msg_type}",
                **name_btn_kwargs,
            ),
            InlineKeyboardButton(
                status,
                callback_data=f"lt:t:{msg_type}",
                style=toggle_style,
            ),
        ])

    # Pagination row — clickable page numbers
    nav_buttons = []
    for p in range(total_pages):
        label = f"• {p + 1} •" if p == page else str(p + 1)
        nav_buttons.append(InlineKeyboardButton(
            label,
            callback_data=f"lt:p:{p}" if p != page else "lt:noop",
        ))
    buttons.append(nav_buttons)

    # Action buttons
    buttons.append([
        InlineKeyboardButton("🔓 Unlock All", callback_data="lt:ua", style=ButtonStyle.SUCCESS),
        InlineKeyboardButton("🔒 Lock All", callback_data="lt:la", style=ButtonStyle.DANGER),
    ])
    buttons.append([
        InlineKeyboardButton("🔄 Default", callback_data="lt:df", style=ButtonStyle.PRIMARY),
    ])
    buttons.append([
        InlineKeyboardButton("❌ Close", callback_data="lt:c", style=ButtonStyle.DANGER),
    ])

    return InlineKeyboardMarkup(buttons)


async def auto_delete_after(message: Message, delay: int = 60):
    """Delete message after delay."""
    await asyncio.sleep(delay)
    try:
        await message.delete()
        # Clean up task reference
        if message.id in _auto_delete_tasks:
            del _auto_delete_tasks[message.id]
    except Exception:
        pass


def schedule_auto_delete(message: Message, delay: int = 60):
    """Schedule auto-delete, cancelling any existing task for this message."""
    msg_id = message.id

    # Cancel existing task if any
    if msg_id in _auto_delete_tasks:
        _auto_delete_tasks[msg_id].cancel()

    # Create new task
    task = asyncio.create_task(auto_delete_after(message, delay))
    _auto_delete_tasks[msg_id] = task


def reset_auto_delete(message: Message, delay: int = 60):
    """Reset auto-delete timer on user interaction."""
    schedule_auto_delete(message, delay)


def _get_current_page(callback: CallbackQuery) -> int:
    """Extract current page number from the active pagination button (• N •)."""
    if callback.message and callback.message.reply_markup:
        for row in callback.message.reply_markup.inline_keyboard:
            for btn in row:
                if btn.callback_data == "lt:noop":
                    try:
                        # Parse "• 2 •" format
                        return int(btn.text.replace("•", "").strip()) - 1
                    except (ValueError, IndexError):
                        pass
    return 0


async def _refresh_keyboard(callback: CallbackQuery, user_id: int, page: int = None):
    """Rebuild and update the keyboard, ignoring MessageNotModified."""
    if page is None:
        page = _get_current_page(callback)
    keyboard = build_locktypes_keyboard(user_id, page)
    try:
        await callback.message.edit_reply_markup(keyboard)
    except MessageNotModified:
        pass


def register_lock_handlers(app: Client) -> None:
    """Register lock/unlock type command handlers."""

    @app.on_message(filters.command("locktypes") & filters.private)
    async def locktypes_cmd(client: Client, message: Message):
        store = get_store()
        uid = message.from_user.id

        if store.is_banned(uid):
            return

        user = store.get_user(uid)
        if not user:
            logger.warning(f"Unregistered user {uid} tried /locktypes")
            await message.reply(
                await gstr("locktypes_no_user", message),
                parse_mode=ParseMode.HTML
            )
            return

        keyboard = build_locktypes_keyboard(uid, 0)
        sent_msg = await message.reply(
            "📋 <b>Message Type Settings</b>\n\n"
            "Click type name for info, toggle button to enable/disable.",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )

        # Schedule auto-delete (resets on each interaction)
        schedule_auto_delete(sent_msg, 60)
        logger.info(f"User {uid} opened locktypes menu")

    @app.on_callback_query(filters.regex(r"^lt:"))
    async def locktypes_callback(client: Client, callback: CallbackQuery):
        store = get_store()
        uid = callback.from_user.id
        data = callback.data

        user = store.get_user(uid)
        if not user:
            await callback.answer("Please /start first", show_alert=True)
            return

        parts = data.split(":")
        action = parts[1]

        # Reset auto-delete timer on any interaction (except close)
        if action != "c" and callback.message:
            reset_auto_delete(callback.message, 60)

        # Toggle type
        if action == "t" and len(parts) > 2:
            msg_type = parts[2]
            allowed = store.get_allowed_types(str(uid))

            if msg_type in allowed:
                await store.lock_type(str(uid), msg_type)
                await callback.answer(f"🚫 {msg_type} locked")
            else:
                await store.unlock_type(str(uid), msg_type)
                await callback.answer(f"✅ {msg_type} unlocked")

            await _refresh_keyboard(callback, uid)

        # Info about type
        elif action == "i" and len(parts) > 2:
            msg_type = parts[2]
            info = TYPE_INFO.get(msg_type, "No description available")
            allowed = store.get_allowed_types(str(uid))
            status = "✅ Allowed" if msg_type in allowed else "🚫 Blocked"
            await callback.answer(f"{msg_type}: {info}\n\nStatus: {status}", show_alert=True)

        # Pagination
        elif action == "p" and len(parts) > 2:
            page = int(parts[2])
            await _refresh_keyboard(callback, uid, page)
            await callback.answer()

        # Unlock all
        elif action == "ua":
            await store.unlock_type(str(uid), "all")
            await callback.answer("✅ All types unlocked")
            await _refresh_keyboard(callback, uid)

        # Default permissions
        elif action == "df":
            await store.reset_allowed_types(str(uid))
            await callback.answer("🔄 Reset to default permissions")
            await _refresh_keyboard(callback, uid)

        # Lock all
        elif action == "la":
            await store.lock_type(str(uid), "all")
            await callback.answer("🚫 All types locked")
            await _refresh_keyboard(callback, uid)

        # Close
        elif action == "c":
            # Cancel auto-delete task
            if callback.message.id in _auto_delete_tasks:
                _auto_delete_tasks[callback.message.id].cancel()
                del _auto_delete_tasks[callback.message.id]
            await callback.message.delete()
            await callback.answer()

        # No-op (page indicator)
        elif action == "noop":
            await callback.answer()
