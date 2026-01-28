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

        # End active connection if any
        if store.get_connection(uid):
            logger.info(f"User {uid} requested blocked list with active connection, terminating")
            await store.end_connection(uid)

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

        # End active connection
        if store.get_connection(uid):
            await store.end_connection(uid)

        args = message.text.split()

        # Block by token
        if len(args) == 2 and not message.reply_to_message:
            token = args[1]
            target_id, target_data = store.get_by_token(token)

            if not target_data:
                logger.warning(f"User {uid} tried to block invalid token: {token}")
                await message.reply(
                    (await gstr("block_invalid_token", message)).format(token=token),
                    parse_mode=ParseMode.HTML
                )
                return

            if target_id == uid:
                logger.warning(f"User {uid} tried to block own token")
                await message.reply(await gstr("block_self", message), parse_mode=ParseMode.HTML)
                return

            try:
                await client.get_chat(target_id)
                if store.is_blocked(recipient, target_data['nickname']):
                    logger.info(f"User {uid} tried to block already blocked: {target_data['nickname']}")
                    await message.reply(
                        (await gstr("block_already_blocked", message)).format(
                            nickname=target_data['nickname']
                        ),
                        parse_mode=ParseMode.HTML
                    )
                    return

                await store.block(recipient, target_data['nickname'], token)
                logger.info(f"User {uid} blocked {target_data['nickname']} (token: {token})")
                await message.reply(
                    (await gstr("block_success", message)).format(nickname=target_data['nickname']),
                    parse_mode=ParseMode.HTML
                )
            except InputUserDeactivated:
                logger.warning(f"User {uid} tried to block deactivated user: {target_id}")
                await message.reply(
                    (await gstr("block_deactivated", message)).format(
                        nickname=target_data['nickname']
                    ),
                    parse_mode=ParseMode.HTML
                )
            return

        # Block by reply
        if not message.reply_to_message:
            logger.warning(f"User {uid} tried /block without reply or token")
            await message.reply(await gstr("block_no_args", message), parse_mode=ParseMode.HTML)
            return

        lines = message.reply_to_message.caption or message.reply_to_message.text or ""
        sender_nickname = extract_nickname_from_message(lines)

        if not sender_nickname:
            logger.warning(f"User {uid} tried to block but couldn't extract nickname")
            await message.reply(await gstr("block_no_nickname", message), parse_mode=ParseMode.HTML)
            return

        target_id = store.find_user_by_nickname(sender_nickname)
        if not target_id:
            logger.warning(f"Nickname {sender_nickname} not found for block by {uid}")
            await message.reply(
                await gstr("block_nickname_not_found", message),
                parse_mode=ParseMode.HTML
            )
            return

        target_data = store.get_user(target_id)
        blocked_token = target_data['token'] if target_data else None

        if not blocked_token:
            await message.reply(
                await gstr("block_nickname_not_found", message),
                parse_mode=ParseMode.HTML
            )
            return

        try:
            await client.get_chat(target_id)
            if store.is_blocked(recipient, sender_nickname):
                logger.info(f"User {uid} tried to block already blocked: {sender_nickname}")
                await message.reply(
                    (await gstr("block_already_blocked", message)).format(nickname=sender_nickname),
                    parse_mode=ParseMode.HTML
                )
                return

            await store.block(recipient, sender_nickname, blocked_token)
            logger.info(f"User {uid} blocked {sender_nickname} (token: {blocked_token})")
            await message.reply(
                (await gstr("block_success", message)).format(nickname=sender_nickname),
                parse_mode=ParseMode.HTML
            )
        except InputUserDeactivated:
            logger.warning(f"User {uid} tried to block deactivated user: {target_id}")
            await message.reply(
                (await gstr("block_deactivated", message)).format(nickname=sender_nickname),
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
