"""Language command handler with inline buttons."""

import asyncio
import logging

from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode, ButtonStyle

from ..store import get_store
from ..strings import gstr, strings

logger = logging.getLogger(__name__)


def _get_lang_display(lang_code: str) -> str:
    """Get display name for a language from its lang_name key, fallback to code."""
    name = strings.strings.get(lang_code, {}).get("lang_name")
    if name:
        return name
    return lang_code.upper()


async def auto_delete_message(message: Message, delay: int = 60):
    """Delete message after delay."""
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except Exception:
        pass


def register_language_handlers(app: Client) -> None:
    """Register language command handler."""

    @app.on_message(filters.command("lang") & filters.private)
    async def lang_cmd(client: Client, message: Message):
        store = get_store()
        uid = message.from_user.id

        if store.is_banned(uid):
            return

        user = store.get_user(uid)
        if not user:
            logger.warning(f"Unregistered user {uid} tried /lang")
            await message.reply(await gstr("lang_no_user", message), parse_mode=ParseMode.HTML)
            return

        available_langs = strings.get_available_languages()
        current_lang = user.get('lang', 'en')

        # Build inline keyboard with available languages
        buttons = []
        row = []
        for lang_code in available_langs:
            display_name = _get_lang_display(lang_code)
            # Mark current language
            if lang_code == current_lang:
                display_name = f"✓ {display_name}"
            row.append(InlineKeyboardButton(
                display_name, callback_data=f"lang:{lang_code}",
            ))
            if len(row) == 2:  # 2 buttons per row
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        # Add cancel button (only colored button)
        buttons.append([InlineKeyboardButton("Close", callback_data="lang:cancel", style=ButtonStyle.DANGER, icon_custom_emoji_id=5985346521103604145)])

        keyboard = InlineKeyboardMarkup(buttons)

        sent_msg = await message.reply(
            await gstr("lang_select", message),
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )

        # Auto-delete after 60 seconds
        asyncio.create_task(auto_delete_message(sent_msg, 60))

    @app.on_callback_query(filters.regex(r"^lang:"))
    async def lang_callback(client: Client, callback: CallbackQuery):
        store = get_store()
        uid = callback.from_user.id
        action = callback.data.split(":")[1]

        if action == "cancel":
            await callback.message.delete()
            await callback.answer()
            return

        lang = action
        available_langs = strings.get_available_languages()

        if lang not in available_langs:
            await callback.answer(
                (await gstr("lang_invalid", callback)).format(languages=', '.join(available_langs)),
                show_alert=True
            )
            return

        user = store.get_user(uid)
        if not user:
            await callback.answer("User not found", show_alert=True)
            await callback.message.delete()
            return

        current_lang = user.get('lang', 'en')
        if lang == current_lang:
            await callback.answer(
                (await gstr("lang_already", callback)).format(language=_get_lang_display(lang)),
            )
            return

        if await store.set_user_language(uid, lang, available_langs):
            logger.info(f"User {uid} changed language to {lang}")
            await callback.message.delete()
            await callback.answer(
                (await gstr("lang_changed", callback)).format(language=_get_lang_display(lang))
            )
        else:
            await callback.answer("Failed to change language", show_alert=True)
