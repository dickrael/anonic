"""Anonymous message handling.

This module handles the core anonymous messaging functionality, including:
- Message type detection
- Message forwarding between users
- Connection management during messaging
- Rate limiting and spam protection
"""

import asyncio
import logging
import re
from datetime import timedelta

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ParseMode, MessageEntityType
from pyrogram.errors import UserIsBlocked, FloodWait, PeerIdInvalid, InputUserDeactivated

from ..store import get_store
from ..strings import gstr
from ..config import config
from ..utils import extract_nickname_from_message
from .common import can_connect

logger = logging.getLogger(__name__)

# Commands to exclude from message handling
EXCLUDED_COMMANDS = [
    "start", "disconnect", "locktypes", "lock", "unlock",
    "blocked", "block", "unblock", "report", "ban", "unban", "lang"
]

# Zalgo detection pattern (combining characters)
ZALGO_PATTERN = re.compile(r'[\u0300-\u036f\u0489]{3,}')
# Cyrillic pattern
CYRILLIC_PATTERN = re.compile(r'[\u0400-\u04FF]')
# Cashtag pattern ($WORD)
CASHTAG_PATTERN = re.compile(r'\$[A-Z]{2,}')
# Emoji pattern
EMOJI_PATTERN = re.compile(
    r'[\U0001F600-\U0001F64F'  # emoticons
    r'\U0001F300-\U0001F5FF'  # symbols & pictographs
    r'\U0001F680-\U0001F6FF'  # transport & map
    r'\U0001F1E0-\U0001F1FF'  # flags
    r'\U00002702-\U000027B0'  # dingbats
    r'\U0001F900-\U0001F9FF'  # supplemental symbols
    r'\U0001FA00-\U0001FA6F'  # chess symbols
    r'\U0001FA70-\U0001FAFF'  # symbols extended
    r'\U00002600-\U000026FF'  # misc symbols
    r']'
)


def get_message_types(message: Message) -> list:
    """Determine all applicable types for a message.

    Args:
        message: Pyrogram Message object

    Returns:
        List of message type strings
    """
    types = []
    text = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or []

    # --- Forward types ---
    if message.forward_origin:
        types.append("forward")
        # Check forward source type
        origin = message.forward_origin
        origin_type = str(type(origin).__name__).lower()
        if "user" in origin_type:
            types.append("forwarduser")
        elif "channel" in origin_type:
            types.append("forwardchannel")
        elif "chat" in origin_type:
            if hasattr(origin, 'sender_chat') and origin.sender_chat:
                if origin.sender_chat.type == "bot":
                    types.append("forwardbot")
                else:
                    types.append("forwardchannel")

    # --- Sticker types ---
    if message.sticker:
        types.append("sticker")
        if message.sticker.is_animated:
            types.append("stickeranimated")
        if message.sticker.is_video:
            types.append("stickeranimated")  # video stickers are also "animated"
        if hasattr(message.sticker, 'premium_animation') and message.sticker.premium_animation:
            types.append("stickerpremium")

    # --- Media types ---
    if message.photo:
        types.append("photo")
    if message.video:
        types.append("video")
    if message.animation:
        types.append("gif")
    if message.voice:
        types.append("voice")
    if message.video_note:
        types.append("videonote")
    if message.audio:
        types.append("audio")
    if message.document and not message.animation:
        types.append("document")
    if message.location:
        types.append("location")
    if message.poll:
        types.append("poll")

    # --- Interactive types ---
    if message.game:
        types.append("game")
    if message.dice:
        types.append("emojigame")
    if message.reply_markup:
        if hasattr(message.reply_markup, 'inline_keyboard'):
            types.append("inline")
            types.append("button")

    # --- External reply ---
    if message.external_reply:
        types.append("externalreply")

    # --- Text content analysis ---
    if text:
        # Base text type
        if not types or types == ["forward"] or "forwarduser" in types:
            types.append("text")

        # Entity-based types
        for entity in entities:
            if entity.type == MessageEntityType.URL:
                types.append("url")
            elif entity.type == MessageEntityType.TEXT_LINK:
                types.append("url")
            elif entity.type == MessageEntityType.EMAIL:
                types.append("email")
            elif entity.type == MessageEntityType.PHONE_NUMBER:
                types.append("phone")
            elif entity.type == MessageEntityType.SPOILER:
                types.append("spoiler")
            elif entity.type == MessageEntityType.CUSTOM_EMOJI:
                types.append("emojicustom")
            elif entity.type == MessageEntityType.CASHTAG:
                types.append("cashtag")

        # Pattern-based detection
        if CASHTAG_PATTERN.search(text) and "cashtag" not in types:
            types.append("cashtag")

        if CYRILLIC_PATTERN.search(text):
            types.append("cyrillic")

        if ZALGO_PATTERN.search(text):
            types.append("zalgo")

        # Emoji detection
        emojis = EMOJI_PATTERN.findall(text)
        if emojis:
            types.append("emoji")
            # Check if message is ONLY emojis
            text_without_emoji = EMOJI_PATTERN.sub('', text).strip()
            if not text_without_emoji:
                types.append("emojionly")

    # Default to text if nothing else
    if not types:
        types.append("text")

    return list(set(types))  # Remove duplicates


def get_primary_type(message: Message) -> str:
    """Get the primary/main type of a message for display purposes."""
    if message.sticker:
        return "sticker"
    if message.photo:
        return "photo"
    if message.video:
        return "video"
    if message.animation:
        return "gif"
    if message.voice:
        return "voice"
    if message.video_note:
        return "videonote"
    if message.audio:
        return "audio"
    if message.document:
        return "document"
    if message.location:
        return "location"
    if message.poll:
        return "poll"
    if message.game:
        return "game"
    if message.dice:
        return "emojigame"
    if message.forward_origin:
        return "forward"
    return "text"


async def send_message_to_target(
    client: Client,
    target_id: int,
    message: Message,
    msg_type: str,
    caption: str
) -> None:
    """Send message to target user based on type.

    Args:
        client: Pyrogram client
        target_id: Target user ID
        message: Original message
        msg_type: Detected message type
        caption: Formatted caption to include
    """
    if msg_type in ("text", "link"):
        await client.send_message(target_id, caption, parse_mode=ParseMode.HTML)

    elif msg_type == "audio":
        await client.send_audio(
            target_id,
            message.audio.file_id,
            caption=caption,
            parse_mode=ParseMode.HTML
        )

    elif msg_type == "photo":
        # FIX: Use message.photo[-1].file_id for largest photo size
        await client.send_photo(
            target_id,
            message.photo[-1].file_id,
            caption=caption,
            parse_mode=ParseMode.HTML
        )

    elif msg_type == "document":
        await client.send_document(
            target_id,
            message.document.file_id,
            caption=caption,
            parse_mode=ParseMode.HTML
        )

    elif msg_type == "forward":
        await message.forward(target_id)
        await client.send_message(target_id, caption, parse_mode=ParseMode.HTML)

    elif msg_type == "gif":
        await client.send_animation(
            target_id,
            message.animation.file_id,
            caption=caption,
            parse_mode=ParseMode.HTML
        )

    elif msg_type == "location":
        # FIX: send_location doesn't support caption, send separately
        await client.send_location(
            target_id,
            message.location.latitude,
            message.location.longitude
        )
        await client.send_message(target_id, caption, parse_mode=ParseMode.HTML)

    elif msg_type == "poll":
        # FIX: send_poll doesn't support caption, send separately
        await client.send_poll(
            target_id,
            message.poll.question,
            [option.text for option in message.poll.options]
        )
        await client.send_message(target_id, caption, parse_mode=ParseMode.HTML)

    elif msg_type == "video":
        await client.send_video(
            target_id,
            message.video.file_id,
            caption=caption,
            parse_mode=ParseMode.HTML
        )

    elif msg_type == "videonote":
        await client.send_video_note(target_id, message.video_note.file_id)
        await client.send_message(target_id, caption, parse_mode=ParseMode.HTML)

    elif msg_type == "voice":
        await client.send_voice(
            target_id,
            message.voice.file_id,
            caption=caption,
            parse_mode=ParseMode.HTML
        )

    elif msg_type == "sticker":
        await client.send_sticker(target_id, message.sticker.file_id)
        await client.send_message(target_id, caption, parse_mode=ParseMode.HTML)

    elif msg_type == "emojigame":
        # Dice, bowling, darts, etc.
        await client.send_dice(target_id, emoji=message.dice.emoji)
        await client.send_message(target_id, caption, parse_mode=ParseMode.HTML)

    elif msg_type == "game":
        # Games can't be forwarded easily, just notify
        await client.send_message(target_id, caption, parse_mode=ParseMode.HTML)

    else:
        # Fallback for any other type - try to forward or send as text
        if message.photo:
            await client.send_photo(
                target_id,
                message.photo[-1].file_id,
                caption=caption,
                parse_mode=ParseMode.HTML
            )
        elif message.video:
            await client.send_video(
                target_id,
                message.video.file_id,
                caption=caption,
                parse_mode=ParseMode.HTML
            )
        elif message.document:
            await client.send_document(
                target_id,
                message.document.file_id,
                caption=caption,
                parse_mode=ParseMode.HTML
            )
        else:
            await client.send_message(target_id, caption, parse_mode=ParseMode.HTML)


def register_messaging_handlers(app: Client) -> None:
    """Register anonymous message handlers."""

    @app.on_message(
        filters.private
        & ~filters.command(EXCLUDED_COMMANDS)
        & (
            filters.text
            | filters.audio
            | filters.photo
            | filters.document
            | filters.forwarded
            | filters.animation
            | filters.location
            | filters.poll
            | filters.video
            | filters.video_note
            | filters.voice
            | filters.media_group
            | filters.sticker
            | filters.dice
            | filters.game
        )
    )
    async def anonymous_handler(client: Client, message: Message):
        store = get_store()
        uid = message.from_user.id
        user = store.get_user(uid)

        if not user:
            logger.warning(f"Unregistered user {uid} tried to send message")
            await message.reply(await gstr("anonymous_no_user", message), parse_mode=ParseMode.HTML)
            return

        if store.is_banned(uid):
            await message.reply(await gstr("banned", message), parse_mode=ParseMode.HTML)
            return

        await store.update_last_activity(uid)

        # Determine all message types
        msg_types = get_message_types(message)
        primary_type = get_primary_type(message)
        logger.info(f"Processing message types {msg_types} (primary: {primary_type}) from user {uid}")

        # Check for blocked types (types that are never allowed)
        for t in msg_types:
            if t in store.BLOCKED_TYPES:
                logger.warning(f"Blocked type '{t}' from user {uid}")
                await message.reply(
                    await gstr("anonymous_unsupported_type", message),
                    parse_mode=ParseMode.HTML
                )
                return

        target_id = None
        old_connection = store.get_connection(uid)
        old_nickname = None
        if old_connection:
            old_user = store.get_user(int(old_connection['target_id']))
            old_nickname = old_user['nickname'] if old_user else None

        # Handle reply to message or external reply (quote)
        reply_text = None
        if message.reply_to_message:
            reply_text = message.reply_to_message.caption or message.reply_to_message.text or ""
        elif message.external_reply:
            # External reply (Telegram quote feature)
            if hasattr(message.external_reply, 'quote') and message.external_reply.quote:
                reply_text = message.external_reply.quote.text or ""
            elif hasattr(message.external_reply, 'text'):
                reply_text = message.external_reply.text or ""

        if reply_text:
            sender_nickname = extract_nickname_from_message(reply_text)
            logger.debug(f"Extracted sender_nickname: {sender_nickname!r}")

            if sender_nickname:
                target_id = store.find_user_by_nickname(sender_nickname)

            # If can't extract target from reply, fall back to current connection
            if not target_id or target_id == uid:
                conn = store.get_connection(uid)
                if conn:
                    target_id = int(conn['target_id'])
                    logger.info(f"Reply target not found, using current connection: {target_id}")
                else:
                    logger.warning(f"Reply target not found for nickname: {sender_nickname!r}")
                    await message.reply(
                        await gstr("anonymous_reply_not_found", message),
                        parse_mode=ParseMode.HTML
                    )
                    return

            try:
                can_connect_result, reason = await can_connect(client, uid, target_id)
                if not can_connect_result:
                    logger.warning(f"Connection blocked: {uid} -> {target_id}, reason: {reason}")
                    target_data = store.get_user(target_id)
                    if reason == "banned":
                        await message.reply(
                            (await gstr("start_connection_failed_frozen", message)).format(
                                nickname=target_data['nickname'] if target_data else "User"
                            ),
                            parse_mode=ParseMode.HTML
                        )
                    elif reason == "self_blocked":
                        await message.reply(
                            (await gstr("start_self_blocked", message)).format(
                                nickname=target_data['nickname'] if target_data else "User"
                            ),
                            parse_mode=ParseMode.HTML
                        )
                    elif reason == "deactivated":
                        await message.reply(
                            (await gstr("start_deactivated", message)).format(
                                nickname=target_data['nickname'] if target_data else "User"
                            ),
                            parse_mode=ParseMode.HTML
                        )
                    else:
                        await message.reply(
                            await gstr("anonymous_blocked", message),
                            parse_mode=ParseMode.HTML
                        )
                    return

                # Switch connection if replying to different user
                if old_connection and old_connection['target_id'] != str(target_id):
                    old_target_id = int(old_connection['target_id'])
                    old_target = store.get_user(old_target_id)
                    if old_target and store.get_message_count(uid) > 0:
                        try:
                            await client.get_chat(old_target_id)
                            target_msg = await gstr("disconnect_by_partner", user_id=old_target_id)
                            target_msg = target_msg.format(nickname=user['nickname'])
                            await client.send_message(
                                old_target_id,
                                target_msg,
                                parse_mode=ParseMode.HTML
                            )
                            logger.info(f"Notified {old_target_id} of connection switch by {uid}")
                        # FIX: Added 'as e' to capture exception
                        except (UserIsBlocked, InputUserDeactivated) as e:
                            logger.info(f"Could not notify {old_target_id}: {type(e).__name__}")
                        except Exception as e:
                            logger.error(f"Failed to notify {old_target_id}: {type(e).__name__}: {e}")

                    await store.start_connection(uid, target_id)
                    logger.info(f"User {uid} switched connection to {target_id}")
                elif not old_connection:
                    await store.start_connection(uid, target_id)
                    logger.info(f"User {uid} established connection with {target_id}")

            except (UserIsBlocked, InputUserDeactivated) as e:
                logger.warning(f"Connection failed {uid} -> {target_id}: {type(e).__name__}")
                await message.reply(
                    (await gstr(
                        "start_deactivated" if isinstance(e, InputUserDeactivated) else "start_blocked",
                        message
                    )).format(nickname=sender_nickname),
                    parse_mode=ParseMode.HTML
                )
                return

        if not target_id:
            # No reply or couldn't extract target - use existing connection
            conn = store.get_connection(uid)
            if not conn:
                logger.warning(f"User {uid} sent message without connection")
                await message.reply(
                    await gstr("anonymous_no_connection", message),
                    parse_mode=ParseMode.HTML
                )
                return

            target_id = int(conn['target_id'])
            try:
                can_connect_result, reason = await can_connect(client, uid, target_id)
                if not can_connect_result:
                    logger.warning(f"Connection invalid: {uid} -> {target_id}, reason: {reason}")
                    target_data = store.get_user(target_id)
                    await store.end_connection(uid)
                    await store.update_last_activity(uid)

                    if reason == "banned":
                        await message.reply(
                            (await gstr("start_connection_failed_frozen", message)).format(
                                nickname=target_data['nickname'] if target_data else "User"
                            ),
                            parse_mode=ParseMode.HTML
                        )
                    elif reason == "self_blocked":
                        await message.reply(
                            (await gstr("start_self_blocked", message)).format(
                                nickname=target_data['nickname'] if target_data else "User"
                            ),
                            parse_mode=ParseMode.HTML
                        )
                    elif reason == "deactivated":
                        await message.reply(
                            (await gstr("start_deactivated", message)).format(
                                nickname=target_data['nickname'] if target_data else "User"
                            ),
                            parse_mode=ParseMode.HTML
                        )
                    else:
                        await message.reply(
                            await gstr("anonymous_blocked", message),
                            parse_mode=ParseMode.HTML
                        )
                    return

            except (UserIsBlocked, InputUserDeactivated) as e:
                logger.warning(f"Connection failed {uid} -> {target_id}: {type(e).__name__}")
                target_data = store.get_user(target_id)
                await store.end_connection(uid)
                await store.update_last_activity(uid)
                await message.reply(
                    (await gstr(
                        "start_deactivated" if isinstance(e, InputUserDeactivated) else "anonymous_blocked",
                        message
                    )).format(nickname=target_data['nickname'] if target_data else "User"),
                    parse_mode=ParseMode.HTML
                )
                return

        # Validate target
        target = store.get_user(target_id)
        if not target:
            logger.warning(f"Invalid target {target_id}")
            await store.end_connection(uid)
            await store.update_last_activity(uid)
            await message.reply(
                await gstr("anonymous_target_not_found", message),
                parse_mode=ParseMode.HTML
            )
            return

        # Check message types allowed
        allowed_types = store.get_allowed_types(str(target_id))
        blocked_types = [t for t in msg_types if t not in allowed_types and t != "text"]
        if blocked_types:
            blocked_type = blocked_types[0]  # Report first blocked type
            logger.info(f"Message type {blocked_type} not allowed for {target_id}")
            await message.reply(
                (await gstr("anonymous_type_blocked", message)).format(type=blocked_type),
                parse_mode=ParseMode.HTML
            )
            return

        # Anti-spam check: 60 messages per minute
        await store.add_message_timestamp(uid, target_id)
        message_count = store.get_message_count_in_window(uid, target_id)
        if message_count > 60:
            ban_duration = timedelta(days=1)
            await store.ban_user(uid, ban_duration)
            logger.warning(f"User {uid} banned for spam: {message_count} msgs/min to {target_id}")
            try:
                report_text = (await gstr("spam_report", message)).format(
                    user_id=uid,
                    nickname=user['nickname'],
                    target_id=target_id,
                    target_nickname=target['nickname'],
                    message_count=message_count
                )
                await client.send_message(
                    config.moderation_chat_id,
                    report_text,
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.error(f"Failed to send spam report: {type(e).__name__}: {e}")
            await message.reply(
                (await gstr("spam_banned", message)).format(duration="1 day"),
                parse_mode=ParseMode.HTML
            )
            return

        # Send message
        try:
            original_caption = message.caption or message.text or ""
            caption = (await gstr("anonymous_caption", message)).format(
                original=original_caption,
                nickname=user['nickname']
            )

            # Add context info
            target_conn = store.get_connection(target_id)
            if target_conn and int(target_conn['target_id']) != uid:
                target_user = store.get_user(int(target_conn['target_id']))
                target_nickname = target_user['nickname'] if target_user else "Unknown"
                caption += "\n\n" + (await gstr("anonymous_switch_info", message)).format(
                    sender_nickname=user['nickname'],
                    current_nickname=target_nickname
                )
            elif not target_conn:
                caption += "\n\n" + (await gstr("anonymous_no_connection_info", message)).format(
                    nickname=target['nickname']
                )

            await send_message_to_target(client, target_id, message, primary_type, caption)

            await store.increment_message_count(uid)
            logger.info(f"Message '{primary_type}' sent from {user['nickname']} ({uid}) to {target_id}")

            reply_text = (await gstr("anonymous_sent", message)).format(nickname=target['nickname'])
            if (message.reply_to_message and old_connection and
                old_connection['target_id'] != str(target_id) and old_nickname):
                reply_text += "\n" + (await gstr("anonymous_switched", message)).format(
                    old_nickname=old_nickname,
                    new_nickname=target['nickname']
                )
            await message.reply(reply_text, parse_mode=ParseMode.HTML)

        except UserIsBlocked:
            logger.warning(f"Message failed: {target_id} blocked bot")
            await store.end_connection(uid)
            await store.update_last_activity(uid)
            await message.reply(
                await gstr("anonymous_target_blocked_bot", message),
                parse_mode=ParseMode.HTML
            )

        except InputUserDeactivated:
            logger.warning(f"Message failed: {target_id} deactivated")
            await store.end_connection(uid)
            await store.update_last_activity(uid)
            await message.reply(
                (await gstr("start_deactivated", message)).format(
                    nickname=target['nickname'] if target else "User"
                ),
                parse_mode=ParseMode.HTML
            )

        except FloodWait as e:
            logger.warning(f"FloodWait: {e.value} seconds")
            await asyncio.sleep(e.value)
            await message.reply(
                await gstr("anonymous_flood_wait", message),
                parse_mode=ParseMode.HTML
            )

        except PeerIdInvalid:
            logger.error(f"Invalid peer ID: {target_id}")
            await store.end_connection(uid)
            await store.update_last_activity(uid)
            await message.reply(
                await gstr("anonymous_invalid_peer", message),
                parse_mode=ParseMode.HTML
            )

        except Exception as e:
            logger.error(f"Message failed: {type(e).__name__}: {e}")
            await message.reply(
                await gstr("anonymous_error", message),
                parse_mode=ParseMode.HTML
            )

    @app.on_message(
        filters.private
        & ~filters.command(EXCLUDED_COMMANDS)
        & (
            filters.contact
            | filters.venue
            | filters.successful_payment
        )
    )
    async def unsupported_handler(client: Client, message: Message):
        store = get_store()
        uid = message.from_user.id
        user = store.get_user(uid)

        if not user:
            logger.warning(f"Unregistered user {uid} sent unsupported message")
            await message.reply(
                await gstr("anonymous_no_user", message),
                parse_mode=ParseMode.HTML
            )
            return

        if store.is_banned(uid):
            await message.reply(await gstr("banned", message), parse_mode=ParseMode.HTML)
            return

        await store.update_last_activity(uid)
        logger.info(f"Unsupported message type from user {uid}")
        await message.reply(
            await gstr("anonymous_unsupported_type", message),
            parse_mode=ParseMode.HTML
        )
