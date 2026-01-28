"""Language command handler."""

import logging

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ParseMode

from ..store import get_store
from ..strings import gstr, strings

logger = logging.getLogger(__name__)


def register_language_handlers(app: Client) -> None:
    """Register language command handler."""

    @app.on_message(filters.command("lang") & filters.private)
    async def lang_cmd(client: Client, message: Message):
        store = get_store()
        uid = message.from_user.id

        if store.is_banned(uid):
            await message.reply(await gstr("banned", message), parse_mode=ParseMode.HTML)
            return

        user = store.get_user(uid)
        if not user:
            logger.warning(f"Unregistered user {uid} tried /lang")
            await message.reply(await gstr("lang_no_user", message), parse_mode=ParseMode.HTML)
            return

        args = message.text.split()[1:]
        if not args:
            await message.reply(await gstr("lang_no_args", message), parse_mode=ParseMode.HTML)
            return

        lang = args[0].lower()
        available_langs = strings.get_available_languages()

        if lang not in available_langs:
            await message.reply(
                (await gstr("lang_invalid", message)).format(languages=', '.join(available_langs)),
                parse_mode=ParseMode.HTML
            )
            return

        if await store.set_user_language(uid, lang, available_langs):
            logger.info(f"User {uid} changed language to {lang}")
            await message.reply(
                (await gstr("lang_success", message)).format(language=lang),
                parse_mode=ParseMode.HTML
            )
        else:
            await message.reply(
                await gstr("lang_invalid", message),
                parse_mode=ParseMode.HTML
            )
