"""Security settings handler (protect_content)."""

import asyncio
import logging

from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode, ButtonStyle

from ..store import get_store
from ..strings import gstr

logger = logging.getLogger(__name__)


async def auto_delete_message(message: Message, delay: int = 60):
    """Delete message after delay."""
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except Exception:
        pass


def register_security_handlers(app: Client) -> None:
    """Register security command handler."""

    @app.on_message(filters.command("security") & filters.private)
    async def security_cmd(client: Client, message: Message):
        store = get_store()
        uid = message.from_user.id

        if store.is_banned(uid):
            return

        user = store.get_user(uid)
        if not user:
            await message.reply(await gstr("security_no_user", message), parse_mode=ParseMode.HTML)
            return

        current_status = store.get_protect_content(uid)
        status_text = "✅ ON" if current_status else "❌ OFF"

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔓 Disable" if current_status else "🔒 Enable",
                    callback_data=f"security:toggle",
                    style=ButtonStyle.DANGER if current_status else ButtonStyle.SUCCESS,
                ),
            ],
            [
                InlineKeyboardButton("Close", callback_data="security:close", style=ButtonStyle.DANGER, icon_custom_emoji_id=5985346521103604145),
            ]
        ])

        sent_msg = await message.reply(
            (await gstr("security_info", message)).format(status=status_text),
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )

        asyncio.create_task(auto_delete_message(sent_msg, 60))
        logger.info(f"User {uid} opened security settings")

    @app.on_callback_query(filters.regex(r"^security:"))
    async def security_callback(client: Client, callback: CallbackQuery):
        store = get_store()
        uid = callback.from_user.id
        action = callback.data.split(":")[1]

        if action == "close":
            await callback.message.delete()
            await callback.answer()
            return

        if action == "toggle":
            user = store.get_user(uid)
            if not user:
                await callback.answer("User not found", show_alert=True)
                await callback.message.delete()
                return

            current_status = store.get_protect_content(uid)
            new_status = not current_status
            await store.set_protect_content(uid, new_status)

            status_text = "✅ ON" if new_status else "❌ OFF"

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔓 Disable" if new_status else "🔒 Enable",
                        callback_data=f"security:toggle",
                        style=ButtonStyle.DANGER if new_status else ButtonStyle.SUCCESS,
                    ),
                ],
                [
                    InlineKeyboardButton("Close", callback_data="security:close", style=ButtonStyle.DANGER, icon_custom_emoji_id=5985346521103604145),
                ]
            ])

            await callback.message.edit_text(
                (await gstr("security_info", callback)).format(status=status_text),
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML
            )

            if new_status:
                await callback.answer(await gstr("security_enabled", callback))
            else:
                await callback.answer(await gstr("security_disabled", callback))

            logger.info(f"User {uid} {'enabled' if new_status else 'disabled'} protect_content")
