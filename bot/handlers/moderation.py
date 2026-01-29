"""Moderation handlers (ban, unban, report)."""

import logging

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ParseMode
from pyrogram.errors import InputUserDeactivated

from ..store import get_store
from ..strings import gstr
from ..config import config
from ..utils import extract_nickname_from_message

logger = logging.getLogger(__name__)


def register_moderation_handlers(app: Client) -> None:
    """Register moderation command handlers."""

    @app.on_message(filters.command("ban") & filters.private)
    async def ban_cmd(client: Client, message: Message):
        store = get_store()
        uid = message.from_user.id

        if store.is_banned(uid):
            await message.reply(await gstr("banned", message), parse_mode=ParseMode.HTML)
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
            await message.reply(await gstr("banned", message), parse_mode=ParseMode.HTML)
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
            await message.reply(await gstr("banned", message), parse_mode=ParseMode.HTML)
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
            await client.send_message(
                config.moderation_chat_id,
                report_text,
                parse_mode=ParseMode.HTML
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
