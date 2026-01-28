"""Start command handler."""

import logging

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ParseMode
from pyrogram.errors import UserIsBlocked, InputUserDeactivated

from ..store import get_store
from ..strings import gstr
from ..utils import generate_token, generate_nickname
from .common import can_connect

logger = logging.getLogger(__name__)


def register_start_handlers(app: Client) -> None:
    """Register start command handler."""

    @app.on_message(filters.command("start") & filters.private)
    async def start_cmd(client: Client, message: Message):
        store = get_store()
        user = message.from_user
        uid = user.id

        logger.info(f"Handling /start from user {uid}, username: {user.username or 'None'}")

        if store.is_banned(uid):
            await message.reply(await gstr("banned", message), parse_mode=ParseMode.HTML)
            return

        user_data = store.get_user(uid)
        if not user_data:
            token = generate_token()
            nickname = generate_nickname()
            await store.add_user(uid, token, nickname)
            logger.info(f"New user registered - ID: {uid}, Nickname: {nickname}")
            user_data = store.get_user(uid)

        args = message.text.split()
        if len(args) == 2:
            token = args[1]
            target_id, target_data = store.get_by_token(token)

            if target_data:
                if uid == target_id:
                    logger.info(f"User {uid} tried to connect with own token")
                    await message.reply(
                        (await gstr("start_self_connect", message)).format(
                            bot_username=client.me.username,
                            token=user_data['token'],
                            nickname=user_data['nickname']
                        ),
                        parse_mode=ParseMode.HTML
                    )
                    return

                try:
                    # Validate target is reachable
                    can_connect_result, reason = await can_connect(client, uid, target_id)
                    if not can_connect_result:
                        logger.warning(f"Connection blocked: {uid} -> {target_id}, reason: {reason}")
                        nickname = target_data['nickname']
                        if reason == "banned":
                            await message.reply(
                                (await gstr("start_connection_failed_frozen", message)).format(nickname=nickname),
                                parse_mode=ParseMode.HTML
                            )
                        elif reason == "self_blocked":
                            await message.reply(
                                (await gstr("start_self_blocked", message)).format(nickname=nickname),
                                parse_mode=ParseMode.HTML
                            )
                        elif reason == "deactivated":
                            await message.reply(
                                (await gstr("start_deactivated", message)).format(nickname=nickname),
                                parse_mode=ParseMode.HTML
                            )
                        elif reason == "frozen":
                            await message.reply(
                                (await gstr("start_frozen", message)).format(nickname=nickname),
                                parse_mode=ParseMode.HTML
                            )
                        else:
                            await message.reply(
                                await gstr("start_blocked", message),
                                parse_mode=ParseMode.HTML
                            )
                        return

                    # Set one-time pending target (cleared after first message)
                    await store.set_pending_target(uid, target_id)
                    logger.info(f"User {uid} pending target set to {target_id} ({target_data['nickname']})")

                    await message.reply(
                        (await gstr("start_connection_established", message)).format(
                            nickname=target_data['nickname']
                        ),
                        parse_mode=ParseMode.HTML
                    )

                except (UserIsBlocked, InputUserDeactivated) as e:
                    logger.warning(f"Connection failed {uid} -> {target_id}: {type(e).__name__}")
                    error_key = "start_deactivated" if isinstance(e, InputUserDeactivated) else "start_blocked"
                    await message.reply(
                        (await gstr(error_key, message)).format(nickname=target_data['nickname']),
                        parse_mode=ParseMode.HTML
                    )
            else:
                logger.warning(f"User {uid} used invalid token: {token}")
                await message.reply(
                    await gstr("start_invalid_token", message),
                    parse_mode=ParseMode.HTML
                )
            return

        logger.info(f"User {uid} returning existing link")
        await message.reply(
            (await gstr("start_no_token", message)).format(
                bot_username=client.me.username,
                token=user_data['token'],
                nickname=user_data['nickname']
            ),
            parse_mode=ParseMode.HTML
        )
