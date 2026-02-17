"""Help command handler — opens Mini App help page."""

import logging

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode, WebAppInfo

from ..store import get_store
from ..strings import gstr
from ..client import get_client

logger = logging.getLogger(__name__)

_bot_username: str = ""


def register_help_handlers(app: Client) -> None:
    """Register help command handler."""

    @app.on_message(filters.command("help") & filters.private)
    async def help_cmd(client: Client, message: Message):
        store = get_store()
        uid = message.from_user.id

        if store.is_banned(uid):
            return

        global _bot_username
        if not _bot_username:
            _bot_username = client.me.username if client.me else "ClearSayBot"

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                text="📖 Open Help",
                url=f"https://t.me/{_bot_username}?startapp=help",
            )]
        ])

        await message.reply(
            await gstr("help_message", message),
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
        logger.info(f"User {uid} requested help")
