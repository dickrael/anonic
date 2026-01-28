"""Block/unblock user handlers."""

import logging

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ParseMode
from pyrogram.errors import InputUserDeactivated

from ..store import get_store
from ..strings import gstr
from ..utils import extract_nickname_from_message

logger = logging.getLogger(__name__)


def register_blocking_handlers(app: Client) -> None:
    """Register block/unblock command handlers."""

    @app.on_message(filters.command("blocked") & filters.private)
    async def blocked_cmd(client: Client, message: Message):
        store = get_store()
        uid = message.from_user.id

        if store.is_banned(uid):
            await message.reply(await gstr("banned", message), parse_mode=ParseMode.HTML)
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
                (await gstr("blocked_list", message)).format(users=', '.join(blocked_users)),
                parse_mode=ParseMode.HTML
            )
        logger.info(f"User {uid} requested blocked list: {len(blocked_users)} users")

    @app.on_message(filters.command("block") & filters.private)
    async def block_cmd(client: Client, message: Message):
        store = get_store()
        uid = message.from_user.id

        if store.is_banned(uid):
            await message.reply(await gstr("banned", message), parse_mode=ParseMode.HTML)
            return

        user = store.get_user(uid)
        if not user:
            logger.warning(f"Unregistered user {uid} tried /block")
            await message.reply(await gstr("block_no_user", message), parse_mode=ParseMode.HTML)
            return

        recipient = str(uid)
        args = message.text.split()
        target_id = None
        target_data = None
        sender_nickname = None

        # Priority 1: Block by token argument
        if len(args) >= 2 and not message.reply_to_message:
            token = args[1]
            target_id, target_data = store.get_by_token(token)

            if not target_data:
                logger.warning(f"User {uid} tried to block invalid token: {token}")
                await message.reply(
                    (await gstr("block_invalid_token", message)).format(token=token),
                    parse_mode=ParseMode.HTML
                )
                return

            sender_nickname = target_data['nickname']

        # Priority 2: Block by reply to message (using message tracking)
        elif message.reply_to_message:
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

        # Priority 3: Block pending target (from deep link)
        if not target_id:
            pending_target_id = store.get_pending_target(uid)
            if pending_target_id:
                target_id = pending_target_id
                target_data = store.get_user(target_id)
                if target_data:
                    sender_nickname = target_data['nickname']

        # No target found
        if not target_id or not target_data:
            logger.warning(f"User {uid} tried /block but no target found")
            await message.reply(await gstr("block_no_args", message), parse_mode=ParseMode.HTML)
            return

        # Can't block yourself
        if target_id == uid:
            logger.warning(f"User {uid} tried to block themselves")
            await message.reply(await gstr("block_self", message), parse_mode=ParseMode.HTML)
            return

        # Check if already blocked
        if store.is_blocked(recipient, sender_nickname):
            logger.info(f"User {uid} tried to block already blocked: {sender_nickname}")
            await message.reply(
                (await gstr("block_already_blocked", message)).format(nickname=sender_nickname),
                parse_mode=ParseMode.HTML
            )
            return

        # Clear pending target if blocking that user
        pending_target_id = store.get_pending_target(uid)
        if pending_target_id and pending_target_id == target_id:
            await store.clear_pending_target(uid)

        # Block the user
        try:
            await client.get_chat(target_id)
            await store.block(recipient, sender_nickname, target_data['token'])
            logger.info(f"User {uid} blocked {sender_nickname} (token: {target_data['token']})")
            await message.reply(
                (await gstr("block_success", message)).format(nickname=sender_nickname),
                parse_mode=ParseMode.HTML
            )
        except InputUserDeactivated:
            # Still block them even if deactivated
            await store.block(recipient, sender_nickname, target_data['token'])
            logger.info(f"User {uid} blocked deactivated user {sender_nickname}")
            await message.reply(
                (await gstr("block_success", message)).format(nickname=sender_nickname),
                parse_mode=ParseMode.HTML
            )

    @app.on_message(filters.command("unblock") & filters.private)
    async def unblock_cmd(client: Client, message: Message):
        store = get_store()
        uid = message.from_user.id

        if store.is_banned(uid):
            await message.reply(await gstr("banned", message), parse_mode=ParseMode.HTML)
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

        if not store.is_user_blocked(recipient, identifier):
            logger.warning(f"Identifier {identifier} not blocked by {uid}")
            await message.reply(
                (await gstr("unblock_not_blocked", message)).format(identifier=identifier),
                parse_mode=ParseMode.HTML
            )
            return

        await store.unblock(recipient, identifier)
        logger.info(f"User {uid} unblocked: {identifier}")
        await message.reply(
            (await gstr("unblock_success", message)).format(identifier=identifier),
            parse_mode=ParseMode.HTML
        )
