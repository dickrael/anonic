"""Moderation handlers (ban, unban, report)."""

import logging

from pyrogram import Client, filters
from pyrogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from pyrogram.enums import ParseMode, ButtonStyle
from pyrogram.errors import InputUserDeactivated, UserIsBlocked, PeerIdInvalid

from ..store import get_store
from ..strings import gstr
from ..config import config
from ..utils import extract_nickname_from_message

logger = logging.getLogger(__name__)


def _ban_allow_buttons(user_id: int) -> InlineKeyboardMarkup:
    """Ban / Allow buttons for a report."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🚫 Ban", callback_data=f"mod:ban:{user_id}",
            style=ButtonStyle.DANGER,
        ),
        InlineKeyboardButton(
            "✅ Allow", callback_data=f"mod:allow:{user_id}",
            style=ButtonStyle.SUCCESS,
        ),
    ]])


def _unban_button(user_id: int) -> InlineKeyboardMarkup:
    """Unban button shown after a ban action."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "✅ Unban", callback_data=f"mod:unban:{user_id}",
            style=ButtonStyle.SUCCESS,
        ),
    ]])


def _unban_allow_buttons(user_id: int) -> InlineKeyboardMarkup:
    """Unban / Allow buttons for spam auto-ban reports."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "✅ Unban", callback_data=f"mod:unban:{user_id}",
            style=ButtonStyle.SUCCESS,
        ),
        InlineKeyboardButton(
            "✅ Allow", callback_data=f"mod:allow:{user_id}",
            style=ButtonStyle.SUCCESS,
        ),
    ]])


def register_moderation_handlers(app: Client) -> None:
    """Register moderation command handlers."""

    @app.on_message(filters.command("ban") & filters.private)
    async def ban_cmd(client: Client, message: Message):
        store = get_store()
        uid = message.from_user.id

        if store.is_banned(uid):
            return

        if uid != config.owner_id:
            logger.warning(f"Unauthorized ban attempt from {uid}")
            await message.reply(await gstr("ban_not_owner", message), parse_mode=ParseMode.HTML)
            return

        args = message.text.split()[1:]
        if not args:
            await message.reply(await gstr("ban_no_args", message), parse_mode=ParseMode.HTML)
            return

        try:
            target_id = int(args[0])
        except ValueError:
            await message.reply(await gstr("ban_no_args", message), parse_mode=ParseMode.HTML)
            return

        target = store.get_user(target_id)
        if not target:
            logger.warning(f"Owner {uid} tried to ban non-existent user: {target_id}")
            await message.reply(
                (await gstr("ban_invalid_user", message)).format(user_id=target_id),
                parse_mode=ParseMode.HTML
            )
            return

        if store.is_banned(target_id):
            logger.info(f"Owner {uid} tried to ban already banned user: {target_id}")
            await message.reply(
                (await gstr("ban_already_banned", message)).format(user_id=target_id),
                parse_mode=ParseMode.HTML
            )
            return

        await store.ban_user(target_id)
        logger.info(f"User {target_id} banned by owner {uid}")
        await message.reply(
            (await gstr("ban_success", message)).format(user_id=target_id),
            parse_mode=ParseMode.HTML
        )

    @app.on_message(filters.command("unban") & filters.private)
    async def unban_cmd(client: Client, message: Message):
        store = get_store()
        uid = message.from_user.id

        if store.is_banned(uid):
            return

        if uid != config.owner_id:
            logger.warning(f"Unauthorized unban attempt from {uid}")
            await message.reply(await gstr("unban_not_owner", message), parse_mode=ParseMode.HTML)
            return

        args = message.text.split()[1:]
        if not args:
            await message.reply(await gstr("unban_no_args", message), parse_mode=ParseMode.HTML)
            return

        try:
            target_id = int(args[0])
        except ValueError:
            await message.reply(await gstr("unban_no_args", message), parse_mode=ParseMode.HTML)
            return

        target = store.get_user(target_id)
        if not target:
            logger.warning(f"Owner {uid} tried to unban non-existent user: {target_id}")
            await message.reply(
                (await gstr("ban_invalid_user", message)).format(user_id=target_id),
                parse_mode=ParseMode.HTML
            )
            return

        if not store.is_banned(target_id):
            logger.info(f"Owner {uid} tried to unban non-banned user: {target_id}")
            await message.reply(
                (await gstr("unban_not_banned", message)).format(user_id=target_id),
                parse_mode=ParseMode.HTML
            )
            return

        await store.unban_user(target_id)
        logger.info(f"User {target_id} unbanned by owner {uid}")
        await message.reply(
            (await gstr("unban_success", message)).format(user_id=target_id),
            parse_mode=ParseMode.HTML
        )

    @app.on_message(filters.command("report") & filters.private)
    async def report_cmd(client: Client, message: Message):
        store = get_store()
        uid = message.from_user.id

        if store.is_banned(uid):
            return

        user = store.get_user(uid)
        if not user:
            logger.warning(f"Unregistered user {uid} tried /report")
            await message.reply(await gstr("report_no_user", message), parse_mode=ParseMode.HTML)
            return

        # Must reply to a message to report
        if not message.reply_to_message:
            await message.reply(await gstr("report_no_reply", message), parse_mode=ParseMode.HTML)
            return

        replied_message = message.reply_to_message
        reply_msg_id = replied_message.id

        # Try to get sender from message tracking first
        reported_id = store.get_message_sender(reply_msg_id)
        sender_nickname = None

        if reported_id:
            reported_user = store.get_user(reported_id)
            sender_nickname = reported_user['nickname'] if reported_user else None
        else:
            # Fallback to nickname extraction
            lines = replied_message.caption or replied_message.text or ""
            sender_nickname = extract_nickname_from_message(lines)
            if sender_nickname:
                reported_id = store.find_user_by_nickname(sender_nickname)

        if not sender_nickname:
            logger.warning(f"User {uid} tried to report but couldn't identify sender")
            await message.reply(await gstr("report_no_nickname", message), parse_mode=ParseMode.HTML)
            return

        # Get special_code for reported user
        reported_special_code = ""
        if reported_id:
            reported_special_code = store.get_user_special_code(reported_id)

        # Get reporter's info
        reporter_nickname = user['nickname']
        reporter_special_code = store.get_user_special_code(uid)

        try:
            forwarded_message = await replied_message.forward(config.moderation_chat_id)
            report_text = (await gstr("report_message", message)).format(
                reporter_nickname=reporter_nickname,
                reporter_code=reporter_special_code,
                reported_nickname=sender_nickname,
                reported_code=reported_special_code or "N/A",
                type=replied_message.media or 'text',
                message_id=forwarded_message.id
            )
            buttons = _ban_allow_buttons(reported_id) if reported_id else None
            await client.send_message(
                config.moderation_chat_id,
                report_text,
                parse_mode=ParseMode.HTML,
                reply_markup=buttons,
            )
            logger.info(
                f"Report from {reporter_nickname} ({reporter_special_code}) about "
                f"{sender_nickname} ({reported_special_code}), message ID: {forwarded_message.id}"
            )
            await message.reply(await gstr("report_success", message), parse_mode=ParseMode.HTML)
        except InputUserDeactivated:
            logger.warning(f"Report failed: moderation chat {config.moderation_chat_id} deactivated")
            await message.reply(
                await gstr("report_deactivated", message),
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Report failed: {type(e).__name__}: {e}")
            await message.reply(await gstr("anonymous_error", message), parse_mode=ParseMode.HTML)

    # --- Callback handler for mod: buttons in moderation chat ---
    @app.on_callback_query(filters.regex(r"^mod:"))
    async def mod_callback(client: Client, callback: CallbackQuery):
        # Only owner can use these buttons
        if callback.from_user.id != config.owner_id:
            await callback.answer("Owner only.", show_alert=True)
            return

        parts = callback.data.split(":")
        if len(parts) != 3:
            await callback.answer("Invalid action.", show_alert=True)
            return

        action = parts[1]
        try:
            target_uid = int(parts[2])
        except ValueError:
            await callback.answer("Invalid user ID.", show_alert=True)
            return

        store = get_store()
        original_text = callback.message.text or callback.message.caption or ""

        if action == "ban":
            target = store.get_user(target_uid)
            if not target:
                await callback.answer(await gstr("mod_user_not_found", callback), show_alert=True)
                return

            if store.is_banned(target_uid):
                await callback.answer(await gstr("mod_already_banned", callback), show_alert=True)
                return

            await store.ban_user(target_uid)
            logger.info(f"User {target_uid} banned via mod button by owner {callback.from_user.id}")

            # Send warning DM to banned user
            try:
                await client.send_message(
                    target_uid,
                    await gstr("ban_warning", callback),
                    parse_mode=ParseMode.HTML,
                )
            except (UserIsBlocked, PeerIdInvalid, InputUserDeactivated) as e:
                logger.warning(f"Could not send ban warning to {target_uid}: {type(e).__name__}")
            except Exception as e:
                logger.error(f"Failed to send ban warning to {target_uid}: {type(e).__name__}: {e}")

            # Edit report message: append status, show Unban button
            new_text = original_text + "\n\n" + (await gstr("mod_banned", callback))
            await callback.message.edit_text(
                new_text,
                parse_mode=ParseMode.HTML,
                reply_markup=_unban_button(target_uid),
            )
            await callback.answer()

        elif action == "allow":
            # Edit report message: append status, remove buttons
            new_text = original_text + "\n\n" + (await gstr("mod_allowed", callback))
            await callback.message.edit_text(
                new_text,
                parse_mode=ParseMode.HTML,
            )
            await callback.answer()

        elif action == "unban":
            target = store.get_user(target_uid)
            if not target:
                await callback.answer(await gstr("mod_user_not_found", callback), show_alert=True)
                return

            if not store.is_banned(target_uid):
                await callback.answer(await gstr("mod_not_banned", callback), show_alert=True)
                return

            await store.unban_user(target_uid)
            logger.info(f"User {target_uid} unbanned via mod button by owner {callback.from_user.id}")

            # Edit report message: append status, show Ban button again
            new_text = original_text + "\n\n" + (await gstr("mod_unbanned", callback))
            await callback.message.edit_text(
                new_text,
                parse_mode=ParseMode.HTML,
                reply_markup=_ban_allow_buttons(target_uid),
            )
            await callback.answer()

        else:
            await callback.answer("Unknown action.", show_alert=True)
