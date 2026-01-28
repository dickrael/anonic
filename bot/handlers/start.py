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
                    can_connect_result, reason = await can_connect(client, uid, target_id)
                    if not can_connect_result:
                        logger.warning(f"Connection blocked: {uid} -> {target_id}, reason: {reason}")
                        if reason == "banned":
                            await message.reply(
                                (await gstr("start_connection_failed_frozen", message)).format(
                                    nickname=target_data['nickname']
                                ),
                                parse_mode=ParseMode.HTML
                            )
                        elif reason == "self_blocked":
                            await message.reply(
                                (await gstr("start_self_blocked", message)).format(
                                    nickname=target_data['nickname']
                                ),
                                parse_mode=ParseMode.HTML
                            )
                        elif reason == "deactivated":
                            await message.reply(
                                (await gstr("start_deactivated", message)).format(
                                    nickname=target_data['nickname']
                                ),
                                parse_mode=ParseMode.HTML
                            )
                        else:
                            await message.reply(
                                await gstr("start_blocked", message),
                                parse_mode=ParseMode.HTML
                            )
                        return

                    old_connection = store.get_connection(uid)
                    await store.start_connection(uid, target_id)
                    logger.info(f"User {uid} connected to {target_id} ({target_data['nickname']})")

                    reply_text = (await gstr("start_connection_established", message)).format(
                        nickname=target_data['nickname']
                    )

                    if old_connection and old_connection['target_id'] != str(target_id):
                        old_user = store.get_user(int(old_connection['target_id']))
                        if old_user:
                            reply_text += "\n" + (await gstr("start_connection_switched", message)).format(
                                old_nickname=old_user['nickname']
                            )

                    await message.reply(reply_text, parse_mode=ParseMode.HTML)

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
