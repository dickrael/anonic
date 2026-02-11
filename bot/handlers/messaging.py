"""Anonymous message handling.

Routing Model:
1. Sender → Receiver (via deep link session)
   - Sender clicks deep link, session is set
   - Next messages from sender go to session target

2. Receiver → Sender (reply flow)
   - Receiver MUST reply to a specific message
   - Bot looks up original sender from message_id
   - Allows handling multiple anonymous senders

3. Unknown message condition
   - No active session AND not replying to stored message
   - Message is rejected with "Unknown message"
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
from .common import can_connect
from .moderation import _unban_allow_buttons

logger = logging.getLogger(__name__)

# Commands to exclude from message handling
EXCLUDED_COMMANDS = [
    "start", "help", "disconnect", "locktypes", "lock", "unlock",
    "blocked", "block", "unblock", "unblockall", "report", "ban", "unban",
    "lang", "revoke", "security", "stats", "adminstats", "temp_link", "activelinks"
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
    """Determine all applicable types for a message."""
    types = []
    text = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or []

    # --- Forward types ---
    if message.forward_origin:
        types.append("forward")
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
            types.append("stickeranimated")  # video stickers grouped with animated
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

    # --- External reply / story ---
    if message.external_reply:
        types.append("externalreply")
    if message.story:
        types.append("forwardstory")

    # --- Text content analysis ---
    if text:
        if not types or types == ["forward"] or "forwarduser" in types:
            types.append("text")

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

        if CASHTAG_PATTERN.search(text) and "cashtag" not in types:
            types.append("cashtag")
        if CYRILLIC_PATTERN.search(text):
            types.append("cyrillic")
        if ZALGO_PATTERN.search(text):
            types.append("zalgo")

        emojis = EMOJI_PATTERN.findall(text)
        if emojis:
            types.append("emoji")
            text_without_emoji = EMOJI_PATTERN.sub('', text).strip()
            if not text_without_emoji:
                types.append("emojionly")

    if not types:
        types.append("text")

    return list(set(types))


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
    caption: str,
    protect_content: bool = False
) -> Message:
    """Send message to target user based on type. Returns the sent message."""
    sent_msg = None

    if msg_type in ("text", "link"):
        sent_msg = await client.send_message(
            target_id, caption, parse_mode=ParseMode.HTML, protect_content=protect_content
        )

    elif msg_type == "audio":
        sent_msg = await client.send_audio(
            target_id,
            message.audio.file_id,
            caption=caption,
            parse_mode=ParseMode.HTML,
            protect_content=protect_content
        )

    elif msg_type == "photo":
        sent_msg = await client.send_photo(
            target_id,
            message.photo[-1].file_id,
            caption=caption,
            parse_mode=ParseMode.HTML,
            protect_content=protect_content
        )

    elif msg_type == "document":
        sent_msg = await client.send_document(
            target_id,
            message.document.file_id,
            caption=caption,
            parse_mode=ParseMode.HTML,
            protect_content=protect_content
        )

    elif msg_type == "forward":
        await message.forward(target_id, protect_content=protect_content)
        sent_msg = await client.send_message(
            target_id, caption, parse_mode=ParseMode.HTML, protect_content=protect_content
        )

    elif msg_type == "gif":
        sent_msg = await client.send_animation(
            target_id,
            message.animation.file_id,
            caption=caption,
            parse_mode=ParseMode.HTML,
            protect_content=protect_content
        )

    elif msg_type == "location":
        await client.send_location(
            target_id,
            message.location.latitude,
            message.location.longitude,
            protect_content=protect_content
        )
        sent_msg = await client.send_message(
            target_id, caption, parse_mode=ParseMode.HTML, protect_content=protect_content
        )

    elif msg_type == "poll":
        await client.send_poll(
            target_id,
            message.poll.question,
            [option.text for option in message.poll.options],
            protect_content=protect_content
        )
        sent_msg = await client.send_message(
            target_id, caption, parse_mode=ParseMode.HTML, protect_content=protect_content
        )

    elif msg_type == "video":
        sent_msg = await client.send_video(
            target_id,
            message.video.file_id,
            caption=caption,
            parse_mode=ParseMode.HTML,
            protect_content=protect_content
        )

    elif msg_type == "videonote":
        await client.send_video_note(
            target_id, message.video_note.file_id, protect_content=protect_content
        )
        sent_msg = await client.send_message(
            target_id, caption, parse_mode=ParseMode.HTML, protect_content=protect_content
        )

    elif msg_type == "voice":
        sent_msg = await client.send_voice(
            target_id,
            message.voice.file_id,
            caption=caption,
            parse_mode=ParseMode.HTML,
            protect_content=protect_content
        )

    elif msg_type == "sticker":
        await client.send_sticker(target_id, message.sticker.file_id, protect_content=protect_content)
        sent_msg = await client.send_message(
            target_id, caption, parse_mode=ParseMode.HTML, protect_content=protect_content
        )

    elif msg_type == "emojigame":
        await client.send_dice(target_id, emoji=message.dice.emoji, protect_content=protect_content)
        sent_msg = await client.send_message(
            target_id, caption, parse_mode=ParseMode.HTML, protect_content=protect_content
        )

    elif msg_type == "game":
        sent_msg = await client.send_message(
            target_id, caption, parse_mode=ParseMode.HTML, protect_content=protect_content
        )

    else:
        # Fallback
        if message.photo:
            sent_msg = await client.send_photo(
                target_id,
                message.photo[-1].file_id,
                caption=caption,
                parse_mode=ParseMode.HTML,
                protect_content=protect_content
            )
        elif message.video:
            sent_msg = await client.send_video(
                target_id,
                message.video.file_id,
                caption=caption,
                parse_mode=ParseMode.HTML,
                protect_content=protect_content
            )
        elif message.document:
            sent_msg = await client.send_document(
                target_id,
                message.document.file_id,
                caption=caption,
                parse_mode=ParseMode.HTML,
                protect_content=protect_content
            )
        else:
            sent_msg = await client.send_message(
                target_id, caption, parse_mode=ParseMode.HTML, protect_content=protect_content
            )

    return sent_msg


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
            return

        await store.update_last_activity(uid)

        # Determine message types
        msg_types = get_message_types(message)
        primary_type = get_primary_type(message)
        logger.info(f"Processing message types {msg_types} (primary: {primary_type}) from user {uid}")

        # Check for blocked types
        for t in msg_types:
            if t in store.BLOCKED_TYPES:
                logger.warning(f"Blocked type '{t}' from user {uid}")
                await message.reply(
                    await gstr("anonymous_unsupported_type", message),
                    parse_mode=ParseMode.HTML
                )
                return

        target_id = None
        target = None

        # ===== ROUTING LOGIC =====
        #
        # Sender (has pending target via deep link): Can send freely
        # Receiver (no pending target): MUST reply to answer
        #

        target_id = None
        target = None
        is_reply_routing = False

        # Priority 1: User has pending target (sender - connected via deep link)
        pending_target_id = store.get_pending_target(uid)
        if pending_target_id:
            target_id = pending_target_id
            target = store.get_user(target_id)
            logger.info(f"Session routing: {uid} -> {target_id}")

        # Priority 2: Reply to a tracked message (receiver must reply)
        elif message.reply_to_message:
            reply_msg_id = message.reply_to_message.id
            original_sender_id = store.get_message_sender(reply_msg_id)

            if original_sender_id:
                target_id = original_sender_id
                target = store.get_user(target_id)
                is_reply_routing = True

                # If user had a pending target (was connected to someone), switch to new target
                old_pending = store.get_pending_target(uid)
                if old_pending and old_pending != target_id:
                    old_target = store.get_user(old_pending)
                    old_nickname = old_target['nickname'] if old_target else "user"
                    await store.set_pending_target(uid, target_id)
                    logger.info(f"User {uid} switched from {old_pending} to {target_id} via reply")
                elif not old_pending:
                    # Set new pending target for the replying user
                    await store.set_pending_target(uid, target_id)
                    logger.info(f"User {uid} now connected to {target_id} via reply")

                logger.info(f"Reply routing: {uid} -> {target_id} (via message {reply_msg_id})")
            else:
                # Reply to unknown/expired message - reject
                logger.warning(f"Reply to unknown message {reply_msg_id} from user {uid}")
                await message.reply(
                    await gstr("anonymous_reply_not_found", message),
                    parse_mode=ParseMode.HTML
                )
                return

        # Priority 3: No route - receiver must reply
        else:
            logger.warning(f"User {uid} sent message without session or reply")
            await message.reply(
                await gstr("anonymous_no_connection", message),
                parse_mode=ParseMode.HTML
            )
            return

        # Validate target exists
        if not target_id or not target:
            logger.warning(f"User {uid} - target not found")
            await message.reply(
                await gstr("anonymous_target_not_found", message),
                parse_mode=ParseMode.HTML
            )
            return

        # ===== VALIDATION =====

        # Check if target is reachable
        try:
            can_connect_result, reason = await can_connect(client, uid, target_id, check_busy=False)
            if not can_connect_result:
                logger.warning(f"Message blocked: {uid} -> {target_id}, reason: {reason}")
                nickname = target['nickname'] if target else "User"
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
                        await gstr("anonymous_blocked", message),
                        parse_mode=ParseMode.HTML
                    )
                return
        except (UserIsBlocked, InputUserDeactivated) as e:
            logger.warning(f"Target unreachable {uid} -> {target_id}: {type(e).__name__}")
            await message.reply(
                (await gstr(
                    "start_deactivated" if isinstance(e, InputUserDeactivated) else "anonymous_blocked",
                    message
                )).format(nickname=target['nickname'] if target else "User"),
                parse_mode=ParseMode.HTML
            )
            return

        # Check message types allowed by target
        allowed_types = store.get_allowed_types(str(target_id))
        blocked_types = [t for t in msg_types if t not in allowed_types and t != "text"]
        if blocked_types:
            blocked_type = blocked_types[0]
            logger.info(f"Message type {blocked_type} not allowed by {target_id}")
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
                    parse_mode=ParseMode.HTML,
                    reply_markup=_unban_allow_buttons(uid),
                )
            except Exception as e:
                logger.error(f"Failed to send spam report: {type(e).__name__}: {e}")
            await message.reply(
                (await gstr("spam_banned", message)).format(duration="1 day"),
                parse_mode=ParseMode.HTML
            )
            return

        # ===== SEND MESSAGE =====
        try:
            original_caption = message.caption or message.text or ""

            # Build caption based on whether there's text content
            if original_caption.strip():
                caption = (await gstr("anonymous_caption", message)).format(
                    original=original_caption,
                    nickname=user['nickname']
                )
            else:
                # No text content (sticker, voice, etc.) - just show sender info
                caption = f"———\n✨ from <b>{user['nickname']}</b>"

            # Add reply instruction ONLY if target has no active session with sender
            target_pending = store.get_pending_target(target_id)
            if target_pending == uid:
                # Target is already connected to sender - no instruction needed
                pass
            elif target_pending:
                # Target is connected to someone else - warn about disconnection
                caption += "\n" + (await gstr("anonymous_reply_warning", message))
            else:
                # Target has no session - show reply instruction
                caption += "\n" + (await gstr("anonymous_reply_instruction", message))

            # Get sender's protect_content setting
            sender_protect_content = store.get_protect_content(uid)

            # Send message to target
            sent_msg = await send_message_to_target(
                client, target_id, message, primary_type, caption,
                protect_content=sender_protect_content
            )

            # Store message for reply routing (so target can reply back)
            if sent_msg:
                await store.store_message(sent_msg.id, uid, target_id)
                logger.debug(f"Stored message {sent_msg.id} for reply routing: {uid} -> {target_id}")

            # Update message stats
            await store.increment_messages_sent(uid)
            await store.increment_messages_received(target_id)

            logger.info(f"Message '{primary_type}' sent from {user['nickname']} ({uid}) to {target_id}")

            await message.reply(
                (await gstr("anonymous_sent", message)).format(nickname=target['nickname']),
                parse_mode=ParseMode.HTML
            )

        except UserIsBlocked:
            logger.warning(f"Message failed: {target_id} blocked bot")
            await message.reply(
                await gstr("anonymous_target_blocked_bot", message),
                parse_mode=ParseMode.HTML
            )

        except InputUserDeactivated:
            logger.warning(f"Message failed: {target_id} deactivated")
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
            return

        await store.update_last_activity(uid)
        logger.info(f"Unsupported message type from user {uid}")
        await message.reply(
            await gstr("anonymous_unsupported_type", message),
            parse_mode=ParseMode.HTML
        )
