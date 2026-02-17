"""Start and revoke command handlers."""

import asyncio
import logging

from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from pyrogram.enums import ParseMode, ButtonStyle
from pyrogram.errors import UserIsBlocked, InputUserDeactivated

from ..config import config
from ..store import get_store
from ..strings import gstr, strings
from ..utils import generate_token, generate_nickname
from ..levels import get_level
from ..webapp import get_random_frame
from .common import can_connect

logger = logging.getLogger(__name__)


async def auto_delete_message(message: Message, delay: int = 60):
    """Delete message after delay."""
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except Exception:
        pass


def _detect_lang(user) -> str:
    """Detect supported language from Telegram user, default to 'en'."""
    user_lang = user.language_code or "en"
    available = strings.get_available_languages()
    if user_lang in available:
        return user_lang
    base = user_lang.split('-')[0] if '-' in user_lang else user_lang
    return base if base in available else "en"


def register_start_handlers(app: Client) -> None:
    """Register start and revoke command handlers."""

    @app.on_message(filters.command("start") & filters.private)
    async def start_cmd(client: Client, message: Message):
        store = get_store()
        user = message.from_user
        uid = user.id

        logger.info(f"Handling /start from user {uid}, username: {user.username or 'None'}")

        if store.is_banned(uid):
            return

        is_new_user = False
        user_data = store.get_user(uid)
        if not user_data:
            token = generate_token()
            nickname = generate_nickname()
            user_lang = _detect_lang(user)
            frame = get_random_frame()
            is_new_user = True
            await store.add_user(
                telegram_id=uid,
                token=token,
                nickname=nickname,
                language_code=user_lang,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
                is_premium=bool(user.is_premium),
                frame=frame,
            )
            logger.info(f"New user registered - ID: {uid}, Nickname: {nickname}, Lang: {user_lang}, Frame: {frame}")
            user_data = store.get_user(uid)
        else:
            # Existing user: auto-update lang if still default "en" and Telegram lang differs
            db_lang = user_data.get("lang", "en")
            tg_lang = _detect_lang(user)
            if db_lang == "en" and tg_lang != "en":
                available = strings.get_available_languages()
                await store.set_user_language(uid, tg_lang, available)
                logger.info(f"Auto-updated user {uid} lang: en -> {tg_lang}")

        args = message.text.split()
        if len(args) == 2:
            token = args[1]

            # First try regular token
            target_id, target_data = store.get_by_token(token)

            # If not found, try temp link
            if not target_data:
                target_id, target_data = store.get_user_by_temp_link(token)
                if target_data:
                    # Increment temp link usage
                    await store.use_temp_link(token)
                    logger.info(f"User {uid} connected via temp link: {token[:8]}...")

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
                                (await gstr("start_connection_failed_frozen", message)).format(nickname=nickname),
                                parse_mode=ParseMode.HTML
                            )
                        else:
                            await message.reply(
                                await gstr("start_blocked", message),
                                parse_mode=ParseMode.HTML
                            )
                        return

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

        if is_new_user:
            # First-time user: welcome message + help button
            help_url = f"{config.webapp_url}/help.html"
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    text="📖 " + await gstr("help_button", message),
                    web_app=WebAppInfo(url=help_url),
                )]
            ])
            await message.reply(
                (await gstr("start_first", message)).format(
                    bot_username=client.me.username,
                    token=user_data['token'],
                    nickname=user_data['nickname'],
                ),
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )
            logger.info(f"New user {uid} received first-start message with help button")
        else:
            # Returning user: standard link message
            logger.info(f"User {uid} returning existing link")
            xp = user_data.get('messages_sent', 0) + user_data.get('messages_received', 0)
            _, level_title = get_level(xp)
            await message.reply(
                (await gstr("start_no_token", message)).format(
                    bot_username=client.me.username,
                    token=user_data['token'],
                    nickname=user_data['nickname'],
                    level_title=level_title
                ),
                parse_mode=ParseMode.HTML,
            )

    @app.on_message(filters.command("revoke") & filters.private)
    async def revoke_cmd(client: Client, message: Message):
        store = get_store()
        uid = message.from_user.id

        if store.is_banned(uid):
            return

        user_data = store.get_user(uid)
        if not user_data:
            await message.reply(await gstr("revoke_no_user", message), parse_mode=ParseMode.HTML)
            return

        # Check weekly limit before showing confirmation
        last_revoke = user_data.get('last_revoke')
        if last_revoke:
            try:
                from datetime import datetime, timezone
                last_revoke_dt = datetime.fromisoformat(last_revoke)
                days_since = (datetime.now(timezone.utc) - last_revoke_dt).days
                if days_since < 7:
                    days_left = 7 - days_since
                    await message.reply(
                        (await gstr("revoke_wait", message)).format(days=days_left),
                        parse_mode=ParseMode.HTML
                    )
                    return
            except ValueError:
                pass

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Yes, revoke", callback_data="revoke:confirm", style=ButtonStyle.DANGER, icon_custom_emoji_id=5427009714745517609),
                InlineKeyboardButton("Cancel", callback_data="revoke:cancel", icon_custom_emoji_id=5985346521103604145),
            ]
        ])

        sent_msg = await message.reply(
            await gstr("revoke_confirm", message),
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )

        asyncio.create_task(auto_delete_message(sent_msg, 60))
        logger.info(f"User {uid} requested revoke confirmation")

    @app.on_callback_query(filters.regex(r"^revoke:"))
    async def revoke_callback(client: Client, callback: CallbackQuery):
        store = get_store()
        uid = callback.from_user.id
        action = callback.data.split(":")[1]

        if action == "cancel":
            await callback.message.delete()
            await callback.answer("Cancelled")
            return

        if action == "confirm":
            user_data = store.get_user(uid)
            if not user_data:
                await callback.answer("User not found", show_alert=True)
                await callback.message.delete()
                return

            new_token = generate_token()
            new_nickname = generate_nickname()
            new_frame = get_random_frame()

            success, error = await store.revoke_user(uid, new_token, new_nickname, new_frame=new_frame)

            if success:
                await callback.message.edit_text(
                    (await gstr("revoke_success", callback)).format(
                        bot_username=client.me.username,
                        token=new_token,
                        nickname=new_nickname
                    ),
                    parse_mode=ParseMode.HTML
                )
                await callback.answer("Revoked successfully!")
                logger.info(f"User {uid} revoked: new nickname {new_nickname}")
            else:
                if error.startswith("wait_"):
                    days = error.split("_")[1]
                    await callback.answer(f"Wait {days} more days", show_alert=True)
                else:
                    await callback.answer("Failed to revoke", show_alert=True)
                await callback.message.delete()
