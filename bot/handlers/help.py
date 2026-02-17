"""Help command handler — opens Mini App help page."""

import logging

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from pyrogram.enums import ParseMode

from ..config import config
from ..store import get_store
from ..strings import gstr

logger = logging.getLogger(__name__)


def register_help_handlers(app: Client) -> None:
    """Register help command handler."""

    @app.on_message(filters.command("help") & filters.private)
    async def help_cmd(client: Client, message: Message):
        store = get_store()
        uid = message.from_user.id

        if store.is_banned(uid):
            return

        help_url = f"{config.webapp_url}/help.html"

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                text="📖 Open Help",
                web_app=WebAppInfo(url=help_url),
            )]
        ])

        await message.reply(
            await gstr("help_message", message),
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
        logger.info(f"User {uid} requested help")
