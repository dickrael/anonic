"""Disconnect command handler."""

import logging

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ParseMode

from ..store import get_store
from ..strings import gstr

logger = logging.getLogger(__name__)


def register_disconnect_handlers(app: Client) -> None:
    """Register disconnect command handler."""

    @app.on_message(filters.command("disconnect") & filters.private)
    async def disconnect_cmd(client: Client, message: Message):
        store = get_store()
        uid = message.from_user.id

        if store.is_banned(uid):
            return

        user = store.get_user(uid)
        if not user:
            logger.warning(f"Unregistered user {uid} tried to disconnect")
            await message.reply(
                await gstr("disconnect_no_user", message),
                parse_mode=ParseMode.HTML
            )
            return

        # Check if user has a pending target (hasn't sent first message yet)
        pending_target_id = store.get_pending_target(uid)
        if pending_target_id:
            target = store.get_user(pending_target_id)
            target_nickname = target['nickname'] if target else "user"

            await store.clear_pending_target(uid)
            logger.info(f"User {uid} cleared pending target to {pending_target_id}")

            await message.reply(
                (await gstr("disconnect_success", message)).format(nickname=target_nickname),
                parse_mode=ParseMode.HTML
            )
        else:
            # No pending target - nothing to disconnect
            logger.info(f"User {uid} tried to disconnect with no pending target")
            await message.reply(
                await gstr("disconnect_no_connection", message),
                parse_mode=ParseMode.HTML
            )
