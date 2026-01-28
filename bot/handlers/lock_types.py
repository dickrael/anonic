"""Lock/unlock message type handlers."""

import logging

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ParseMode

from ..store import get_store
from ..strings import gstr

logger = logging.getLogger(__name__)


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

        user_allowed_types = store.get_allowed_types(str(uid))

        # Categorized types for better display
        categories = {
            "📝 Content": ["text", "url", "email", "phone", "cashtag", "spoiler"],
            "🔤 Text Filters": ["emoji", "emojionly", "emojicustom", "cyrillic", "zalgo"],
            "📷 Media": ["photo", "video", "gif", "voice", "videonote", "audio", "document"],
            "🎭 Stickers": ["sticker", "stickeranimated", "stickerpremium"],
            "🎮 Interactive": ["location", "poll", "inline", "button", "game", "emojigame"],
            "↩️ Forwards": ["forward", "forwardbot", "forwardchannel", "forwardstory", "forwarduser"],
            "📎 Other": ["externalreply"],
        }

        lines = ["📋 <b>Message Type Settings</b>\n"]
        for category, types in categories.items():
            category_types = []
            for t in types:
                if t in store.VALID_TYPES:
                    status = "✅" if t in user_allowed_types else "🚫"
                    category_types.append(f"{t} {status}")
            if category_types:
                lines.append(f"\n{category}")
                lines.append(", ".join(category_types))

        lines.append("\n\n<i>Use /lock -type or /unlock -type to change</i>")
        lines.append("<i>Use /lock -all or /unlock -all for all types</i>")

        await message.reply("\n".join(lines), parse_mode=ParseMode.HTML)
        logger.info(f"User {uid} requested lock types")

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
