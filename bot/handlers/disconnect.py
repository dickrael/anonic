"""Disconnect command handler."""

import logging

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ParseMode
from pyrogram.errors import UserIsBlocked, InputUserDeactivated

from ..store import get_store
from ..strings import gstr
from ..client import get_client

logger = logging.getLogger(__name__)


def register_disconnect_handlers(app: Client) -> None:
    """Register disconnect command handler."""

    @app.on_message(filters.command("disconnect") & filters.private)
    async def disconnect_cmd(client: Client, message: Message):
        store = get_store()
        uid = message.from_user.id

        if store.is_banned(uid):
            await message.reply(await gstr("banned", message), parse_mode=ParseMode.HTML)
            return

        user = store.get_user(uid)
        if not user:
            logger.warning(f"Unregistered user {uid} tried to disconnect")
            await message.reply(
                await gstr("disconnect_no_user", message),
                parse_mode=ParseMode.HTML
            )
            return

        conn = store.get_connection(uid)
        if not conn:
            logger.info(f"User {uid} tried to disconnect with no active connection")
            await message.reply(
                await gstr("disconnect_no_connection", message),
                parse_mode=ParseMode.HTML
            )
            return

        target_id = int(conn['target_id'])
        target = store.get_user(target_id)

        if not target:
            await store.end_connection(uid)
            logger.info(f"User {uid} disconnected from invalid target {target_id}")
            await message.reply(
                (await gstr("disconnect_success", message)).format(nickname="user"),
                parse_mode=ParseMode.HTML
            )
            return

        # Notify user A (initiator)
        try:
            await message.reply(
                (await gstr("disconnect_success", message)).format(nickname=target['nickname']),
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Failed to notify user {uid} of disconnection: {type(e).__name__}: {e}")

        # Notify user B (target) only if messages were sent and neither user blocked the other
        message_count = store.get_message_count(uid)
        if message_count > 0 and not (
            store.is_blocked(str(uid), target['nickname']) or
            store.is_blocked(str(target_id), user['nickname'])
        ):
            try:
                await client.get_chat(target_id)
                target_msg = await gstr("disconnect_by_partner", user_id=target_id)
                target_msg = target_msg.format(nickname=user['nickname'])
                await client.send_message(target_id, target_msg, parse_mode=ParseMode.HTML)
                logger.info(f"Notified {target_id} of disconnection by {uid}")
            except UserIsBlocked:
                logger.info(f"Could not notify {target_id}: UserIsBlocked")
            except InputUserDeactivated:
                logger.info(f"Could not notify {target_id}: InputUserDeactivated")
            except Exception as e:
                logger.error(f"Failed to notify {target_id}: {type(e).__name__}: {e}")

        # Terminate connections
        await store.end_connection(uid)
        await store.update_last_activity(uid)
        logger.info(f"User {uid} disconnected from {target_id}")
