"""Block/unblock user handlers."""

import asyncio
import logging

from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode, ButtonStyle
from pyrogram.errors import InputUserDeactivated

from ..store import get_store
from ..strings import gstr
from ..utils import extract_nickname_from_message

logger = logging.getLogger(__name__)


async def auto_delete_message(message: Message, delay: int = 60):
    """Delete message after delay."""
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except Exception:
        pass


def register_blocking_handlers(app: Client) -> None:
    """Register block/unblock command handlers."""

    @app.on_message(filters.command("blocked") & filters.private)
    async def blocked_cmd(client: Client, message: Message):
        store = get_store()
        uid = message.from_user.id

        if store.is_banned(uid):
            return

        user = store.get_user(uid)
        if not user:
            logger.warning(f"Unregistered user {uid} tried /blocked")
            await message.reply(
                await gstr("blocked_no_user", message),
                parse_mode=ParseMode.HTML
            )
            return

        blocked_users = store.get_blocked_users(str(uid))
        if not blocked_users:
            await message.reply(await gstr("blocked_none", message), parse_mode=ParseMode.HTML)
        else:
            await message.reply(
                (await gstr("blocked_list", message)).format(users='\n'.join(blocked_users)),
                parse_mode=ParseMode.HTML
            )
        logger.info(f"User {uid} requested blocked list: {len(blocked_users)} users")

    @app.on_message(filters.command("block") & filters.private)
    async def block_cmd(client: Client, message: Message):
        store = get_store()
        uid = message.from_user.id

        if store.is_banned(uid):
            return

        user = store.get_user(uid)
        if not user:
            logger.warning(f"Unregistered user {uid} tried /block")
            await message.reply(await gstr("block_no_user", message), parse_mode=ParseMode.HTML)
            return

        # Must reply to a message to block
        if not message.reply_to_message:
            await message.reply(await gstr("block_no_reply", message), parse_mode=ParseMode.HTML)
            return

        recipient = str(uid)
        target_id = None
        target_data = None
        sender_nickname = None

        # Get sender from message tracking
        reply_msg_id = message.reply_to_message.id
        original_sender_id = store.get_message_sender(reply_msg_id)

        if original_sender_id:
            target_id = original_sender_id
            target_data = store.get_user(target_id)
            if target_data:
                sender_nickname = target_data['nickname']
        else:
            # Fallback: try to extract from message text
            lines = message.reply_to_message.caption or message.reply_to_message.text or ""
            sender_nickname = extract_nickname_from_message(lines)
            if sender_nickname:
                target_id = store.find_user_by_nickname(sender_nickname)
                if target_id:
                    target_data = store.get_user(target_id)

        # No target found
        if not target_id or not target_data:
            logger.warning(f"User {uid} tried /block but no target found")
            await message.reply(await gstr("block_no_reply", message), parse_mode=ParseMode.HTML)
            return

        # Can't block yourself
        if target_id == uid:
            logger.warning(f"User {uid} tried to block themselves")
            await message.reply(await gstr("block_self", message), parse_mode=ParseMode.HTML)
            return

        # Check if already blocked by user_id
        if store.is_blocked_by_user_id(recipient, target_id):
            logger.info(f"User {uid} tried to block already blocked user: {target_id}")
            await message.reply(
                (await gstr("block_already_blocked", message)).format(nickname=sender_nickname),
                parse_mode=ParseMode.HTML
            )
            return

        # Clear pending target if blocking that user
        pending_target_id = store.get_pending_target(uid)
        if pending_target_id and pending_target_id == target_id:
            await store.clear_pending_target(uid)

        # Block the user by user_id
        try:
            await client.get_chat(target_id)
            await store.block(recipient, target_id, sender_nickname)
            logger.info(f"User {uid} blocked {sender_nickname} (user_id: {target_id})")
            await message.reply(
                (await gstr("block_success", message)).format(nickname=sender_nickname),
                parse_mode=ParseMode.HTML
            )
        except InputUserDeactivated:
            # Still block them even if deactivated
            await store.block(recipient, target_id, sender_nickname)
            logger.info(f"User {uid} blocked deactivated user {sender_nickname} (user_id: {target_id})")
            await message.reply(
                (await gstr("block_success", message)).format(nickname=sender_nickname),
                parse_mode=ParseMode.HTML
            )

    @app.on_message(filters.command("unblock") & filters.private)
    async def unblock_cmd(client: Client, message: Message):
        store = get_store()
        uid = message.from_user.id

        if store.is_banned(uid):
            return

        user = store.get_user(uid)
        if not user:
            logger.warning(f"Unregistered user {uid} tried /unblock")
            await message.reply(await gstr("unblock_no_user", message), parse_mode=ParseMode.HTML)
            return

        args = message.text.split()[1:]
        if not args:
            await message.reply(await gstr("unblock_no_args", message), parse_mode=ParseMode.HTML)
            return

        identifier = args[0]
        recipient = str(uid)

        # Check if user is blocked before trying to unblock
        if not store.is_user_blocked(recipient, identifier):
            logger.warning(f"Identifier {identifier} not blocked by {uid}")
            await message.reply(
                (await gstr("unblock_not_blocked", message)).format(identifier=identifier),
                parse_mode=ParseMode.HTML
            )
            return

        # Get nickname + code before unblocking
        entry = store.get_blocked_entry(recipient, identifier)
        nickname = entry["nickname"] if entry else identifier
        code = entry.get("special_code", "") if entry else ""

        await store.unblock(recipient, identifier)
        logger.info(f"User {uid} unblocked: {identifier}")
        await message.reply(
            (await gstr("unblock_success", message)).format(
                nickname=nickname, code=code
            ),
            parse_mode=ParseMode.HTML
        )

    @app.on_message(filters.command("unblockall") & filters.private)
    async def unblockall_cmd(client: Client, message: Message):
        store = get_store()
        uid = message.from_user.id

        if store.is_banned(uid):
            return

        user = store.get_user(uid)
        if not user:
            logger.warning(f"Unregistered user {uid} tried /unblockall")
            await message.reply(await gstr("unblock_no_user", message), parse_mode=ParseMode.HTML)
            return

        # Check if there are any blocked users
        blocked_count = store.get_blocked_count(str(uid))
        if blocked_count == 0:
            await message.reply(await gstr("blocked_none", message), parse_mode=ParseMode.HTML)
            return

        # Show confirmation with count
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅", callback_data="unblockall:confirm", style=ButtonStyle.SUCCESS),
                InlineKeyboardButton("❌", callback_data="unblockall:cancel", style=ButtonStyle.DANGER, icon_custom_emoji_id=5985346521103604145),
            ]
        ])

        sent_msg = await message.reply(
            (await gstr("unblockall_confirm", message)).format(count=blocked_count),
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )

        # Auto-delete after 60 seconds
        asyncio.create_task(auto_delete_message(sent_msg, 60))
        logger.info(f"User {uid} requested unblockall confirmation ({blocked_count} users)")

    @app.on_callback_query(filters.regex(r"^unblockall:"))
    async def unblockall_callback(client: Client, callback: CallbackQuery):
        store = get_store()
        uid = callback.from_user.id
        action = callback.data.split(":")[1]

        if action == "cancel":
            await callback.message.delete()
            await callback.answer()
            return

        if action == "confirm":
            recipient = str(uid)
            count = await store.unblock_all(recipient)

            await callback.message.delete()
            await callback.answer(
                (await gstr("unblockall_success", callback)).format(count=count)
            )
            logger.info(f"User {uid} unblocked all: {count} users")
