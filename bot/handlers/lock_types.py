"""Lock/unlock message type handlers with inline keyboard UI."""

import asyncio
import logging
from typing import Dict, List

from pyrogram import Client, filters
from pyrogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
)
from pyrogram.enums import ParseMode

from ..store import get_store
from ..strings import gstr

logger = logging.getLogger(__name__)

# Items per page for pagination
ITEMS_PER_PAGE = 8

# Type descriptions for info buttons
TYPE_INFO: Dict[str, str] = {
    "all": "Toggle all message types at once",
    # Content
    "text": "Plain text messages (always allowed)",
    "url": "Messages containing URLs/links",
    "email": "Messages containing email addresses",
    "phone": "Messages containing phone numbers",
    "cashtag": "Cash tags like $USD, $BTC",
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
    "stickerpremium": "Premium stickers",
    # Interactive
    "location": "Location sharing",
    "poll": "Polls",
    "inline": "Messages with inline keyboards",
    "button": "Messages with buttons",
    "game": "Telegram games",
    "emojigame": "Dice, bowling, darts mini-games",
    # Forwards
    "forward": "Any forwarded message",
    "forwardbot": "Messages forwarded from bots",
    "forwardchannel": "Messages forwarded from channels",
    "forwardstory": "Forwarded stories",
    "forwarduser": "Messages forwarded from users",
    # Other
    "externalreply": "External reply/quote messages",
}

# Categorized types for organized display
TYPE_CATEGORIES = [
    ("📝", ["url", "email", "phone", "cashtag", "spoiler"]),
    ("🔤", ["emoji", "emojionly", "emojicustom", "cyrillic", "zalgo"]),
    ("📷", ["photo", "video", "gif", "voice", "videonote", "audio", "document"]),
    ("🎭", ["sticker", "stickeranimated", "stickerpremium"]),
    ("🎮", ["location", "poll", "inline", "button", "game", "emojigame"]),
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

        # Find category emoji for this type
        cat_emoji = "📎"
        for emoji, types in TYPE_CATEGORIES:
            if msg_type in types:
                cat_emoji = emoji
                break

        buttons.append([
            InlineKeyboardButton(
                f"{cat_emoji} {msg_type}",
                callback_data=f"lt:i:{msg_type}"
            ),
            InlineKeyboardButton(
                status,
                callback_data=f"lt:t:{msg_type}"
            ),
        ])

    # Pagination row
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("◀️", callback_data=f"lt:p:{page-1}"))
    nav_buttons.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="lt:noop"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("▶️", callback_data=f"lt:p:{page+1}"))
    buttons.append(nav_buttons)

    # Toggle all and close buttons
    buttons.append([
        InlineKeyboardButton("🔓 Unlock All", callback_data="lt:ua"),
        InlineKeyboardButton("🔒 Lock All", callback_data="lt:la"),
    ])
    buttons.append([
        InlineKeyboardButton("❌ Close", callback_data="lt:c"),
    ])

    return InlineKeyboardMarkup(buttons)


async def auto_delete_message(message: Message, delay: int = 60):
    """Delete message after delay."""
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except Exception:
        pass  # Message might already be deleted


def register_lock_handlers(app: Client) -> None:
    """Register lock/unlock type command handlers."""

    @app.on_message(filters.command("locktypes") & filters.private)
    async def locktypes_cmd(client: Client, message: Message):
        store = get_store()
        uid = message.from_user.id

        if store.is_banned(uid):
            await message.reply(await gstr("banned", message), parse_mode=ParseMode.HTML)
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
            "Click type name for info, toggle button to enable/disable.\n"
            "<i>Auto-closes in 60 seconds</i>",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )

        # Schedule auto-delete
        asyncio.create_task(auto_delete_message(sent_msg, 60))
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

            # Get current page from button
            current_page = 0
            if callback.message.reply_markup:
                for row in callback.message.reply_markup.inline_keyboard:
                    for btn in row:
                        if btn.callback_data and btn.callback_data.startswith("lt:p:"):
                            # This is a nav button, extract nearby page info
                            pass
                        if btn.callback_data == "lt:noop":
                            # Page indicator like "2/5"
                            try:
                                current_page = int(btn.text.split("/")[0]) - 1
                            except:
                                pass

            keyboard = build_locktypes_keyboard(uid, current_page)
            await callback.message.edit_reply_markup(keyboard)

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
            keyboard = build_locktypes_keyboard(uid, page)
            await callback.message.edit_reply_markup(keyboard)
            await callback.answer()

        # Unlock all
        elif action == "ua":
            await store.unlock_type(str(uid), "all")
            await callback.answer("✅ All types unlocked")
            # Stay on current page
            current_page = 0
            if callback.message.reply_markup:
                for row in callback.message.reply_markup.inline_keyboard:
                    for btn in row:
                        if btn.callback_data == "lt:noop":
                            try:
                                current_page = int(btn.text.split("/")[0]) - 1
                            except:
                                pass
            keyboard = build_locktypes_keyboard(uid, current_page)
            await callback.message.edit_reply_markup(keyboard)

        # Lock all
        elif action == "la":
            await store.lock_type(str(uid), "all")
            await callback.answer("🚫 All types locked")
            current_page = 0
            if callback.message.reply_markup:
                for row in callback.message.reply_markup.inline_keyboard:
                    for btn in row:
                        if btn.callback_data == "lt:noop":
                            try:
                                current_page = int(btn.text.split("/")[0]) - 1
                            except:
                                pass
            keyboard = build_locktypes_keyboard(uid, current_page)
            await callback.message.edit_reply_markup(keyboard)

        # Close
        elif action == "c":
            await callback.message.delete()
            await callback.answer("Closed")

        # No-op (page indicator)
        elif action == "noop":
            await callback.answer()

    @app.on_message(filters.command("lock") & filters.private)
    async def lock_cmd(client: Client, message: Message):
        store = get_store()
        uid = message.from_user.id

        if store.is_banned(uid):
            await message.reply(await gstr("banned", message), parse_mode=ParseMode.HTML)
            return

        user = store.get_user(uid)
        if not user:
            logger.warning(f"Unregistered user {uid} tried /lock")
            await message.reply(await gstr("lock_no_user", message), parse_mode=ParseMode.HTML)
            return

        args = message.text.split()[1:]
        if not args:
            await message.reply(await gstr("lock_no_args", message), parse_mode=ParseMode.HTML)
            return

        locked_types = []
        invalid_types = []

        if "-all" in args:
            if await store.lock_type(str(uid), "all"):
                locked_types.append("all")
            args = [arg for arg in args if arg != "-all"]

        for msg_type in args:
            msg_type = msg_type.lstrip('-').lower()
            if msg_type == "text":
                invalid_types.append(msg_type)
            elif await store.lock_type(str(uid), msg_type):
                if msg_type not in locked_types:
                    locked_types.append(msg_type)
            else:
                invalid_types.append(msg_type)

        response = ""
        if locked_types:
            response += (await gstr("lock_success", message)).format(types=', '.join(locked_types)) + "\n"
        if invalid_types:
            response += (await gstr("lock_invalid_types", message)).format(types=', '.join(invalid_types))

        await message.reply(response or await gstr("lock_no_args", message), parse_mode=ParseMode.HTML)
        logger.info(f"User {uid} locked types: {locked_types}, ignored: {invalid_types}")

    @app.on_message(filters.command("unlock") & filters.private)
    async def unlock_cmd(client: Client, message: Message):
        store = get_store()
        uid = message.from_user.id

        if store.is_banned(uid):
            await message.reply(await gstr("banned", message), parse_mode=ParseMode.HTML)
            return

        user = store.get_user(uid)
        if not user:
            logger.warning(f"Unregistered user {uid} tried /unlock")
            await message.reply(await gstr("unlock_no_user", message), parse_mode=ParseMode.HTML)
            return

        args = message.text.split()[1:]
        if not args:
            await message.reply(await gstr("unlock_no_args", message), parse_mode=ParseMode.HTML)
            return

        unlocked_types = []
        invalid_types = []

        if "-all" in args:
            if await store.unlock_type(str(uid), "all"):
                unlocked_types.append("all")
            args = [arg for arg in args if arg != "-all"]

        for msg_type in args:
            msg_type = msg_type.lstrip('-').lower()
            if msg_type == "text":
                invalid_types.append(msg_type)
            elif await store.unlock_type(str(uid), msg_type):
                if msg_type not in unlocked_types:
                    unlocked_types.append(msg_type)
            else:
                invalid_types.append(msg_type)

        response = ""
        if unlocked_types:
            response += (await gstr("unlock_success", message)).format(types=', '.join(unlocked_types)) + "\n"
        if invalid_types:
            response += (await gstr("unlock_invalid_types", message)).format(types=', '.join(invalid_types))

        await message.reply(response or await gstr("unlock_no_args", message), parse_mode=ParseMode.HTML)
        logger.info(f"User {uid} unlocked types: {unlocked_types}, ignored: {invalid_types}")
