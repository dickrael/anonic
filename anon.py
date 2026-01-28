import asyncio
import logging
import os
import json
import random
import sys
import string
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List
from dotenv import load_dotenv
from pyrogram import Client, filters, idle
from pyrogram.types import Message
from pyrogram.errors import UserIsBlocked, FloodWait, PeerIdInvalid, InputUserDeactivated
from pyrogram.enums import ParseMode, MessageEntityType
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import yaml

# ------------------ Environment ------------------
load_dotenv()
API_ID_STR = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
MODERATION_CHAT = os.getenv("MODERATION_CHAT_ID")
OWNER_ID_STR = os.getenv("OWNER_ID")
DATA_FILE = os.getenv("DATA_FILE", "data.json")

# Validate required env vars
missing = []
if not API_ID_STR:
    missing.append("API_ID")
if not API_HASH:
    missing.append("API_HASH")
if not BOT_TOKEN:
    missing.append("BOT_TOKEN")
if not MODERATION_CHAT:
    missing.append("MODERATION_CHAT_ID")
if not OWNER_ID_STR:
    missing.append("OWNER_ID")
if missing:
    sys.stderr.write(f"Missing required environment variables: {', '.join(missing)}\n")
    sys.exit(1)

try:
    API_ID = int(API_ID_STR)
    OWNER_ID = int(OWNER_ID_STR)
except ValueError:
    sys.stderr.write("API_ID and OWNER_ID must be integers.\n")
    sys.exit(1)

# ------------------ Nicknames JSON ------------------
NICKNAMES_FILE = os.path.join("assets", "nicknames.json")
try:
    with open(NICKNAMES_FILE, 'r') as f:
        nick_data = json.load(f)
    FIRST_PARTS = nick_data.get("first_parts", [])
    SECOND_PARTS = nick_data.get("second_parts", [])
except Exception as e:
    sys.stderr.write(f"Failed to load nicknames JSON: {e}\n")
    sys.exit(1)

# ------------------ Logging ------------------
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ------------------ Language Handling ------------------
class Strings:
    def __init__(self):
        self.strings = {}
        self.reload_strings()

    def reload_strings(self):
        """Load language strings from YAML files."""
        os.makedirs("langs", exist_ok=True)
        for file in os.listdir("langs"):
            if file.endswith(".yml"):
                lang_code = file[:-4]
                with open(os.path.join("langs", file), "r") as f:
                    self.strings[lang_code] = yaml.safe_load(f) or {}
                logger.info("Loaded strings for %s: %s", lang_code, self.strings[lang_code])
        logger.info("Languages reloaded: %s", list(self.strings.keys()))

    async def gstr(self, string: str, message: Message = None, user_id: int = None) -> str:
        """Get localized string based on user's language from message or user_id."""
        if message and user_id:
            raise ValueError("Provide either message or user_id, not both")
        if not message and not user_id:
            raise ValueError("Either message or user_id must be provided")
        if message:
            user_id = message.from_user.id
        lang = store.get_user_language(user_id) or "en"
        result = self.strings.get(lang, {}).get(string)
        if result is None:
            logger.warning(f"Missing string '{string}' in language '{lang}'")
            result = f"Missing string: {string}"  # Fallback
        logger.debug("Retrieved string for key '%s': %s", string, result)
        return result

strings = Strings()

# ------------------ Data Management ------------------
class JSONStore:
    def __init__(self, path: str):
        self.path = path
        self.valid_types = [
            "all", "audio", "album", "document", "forward", "gif", "location",
            "photo", "poll", "video", "videonote", "voice", "text", "link"
        ]
        self.unsupported_types = [
            "sticker", "contact", "dice", "game", "venue", "successful_payment"
        ]
        if not os.path.exists(path):
            with open(path, 'w') as f:
                json.dump({"users": {}, "blocks": {}, "connections": {}}, f)
        self._load()

    def _load(self):
        with open(self.path, 'r') as f:
            self.data = json.load(f)

    def save(self):
        with open(self.path, 'w') as f:
            json.dump(self.data, f, indent=2, default=str)

    def add_user(self, telegram_id: int, token: str, nickname: str):
        self.data['users'][str(telegram_id)] = {
            "token": token,
            "nickname": nickname,
            "registered_at": datetime.now(timezone.utc).isoformat(),
            "allowed_types": ["text"],
            "last_activity": datetime.now(timezone.utc).isoformat(),
            "lang": "en",
            "banned": False,
            "ban_expires_at": None,
            "message_timestamps": {}  # {target_id: [timestamps]}
        }
        self.save()

    def update_last_activity(self, telegram_id: int):
        user = self.data['users'].get(str(telegram_id))
        if user:
            user['last_activity'] = datetime.now(timezone.utc).isoformat()
            self.save()

    def get_inactive_users(self, timeout_minutes: int = 5) -> List[Dict[str, Any]]:
        inactive_users = []
        current_time = datetime.now(timezone.utc)
        processed_users = set()  # Track processed users to avoid duplicates
        for uid, user in self.data['users'].items():
            if user.get('banned', False) or self.is_banned(int(uid)):
                continue
            last_activity_str = user.get('last_activity')
            if not last_activity_str:
                continue
            last_activity = datetime.fromisoformat(last_activity_str)
            if (current_time - last_activity).total_seconds() / 60 > timeout_minutes:
                if str(uid) in self.data['connections'] and uid not in processed_users:
                    inactive_users.append({"user_id": int(uid), "user_data": user})
                    processed_users.add(int(uid))
        return inactive_users

    def get_user(self, telegram_id: int) -> Dict[str, Any]:
        return self.data['users'].get(str(telegram_id))

    def get_user_language(self, telegram_id: int) -> str:
        user = self.get_user(telegram_id)
        return user.get('lang', 'en') if user else 'en'

    def set_user_language(self, telegram_id: int, lang: str) -> bool:
        user = self.get_user(telegram_id)
        if user and lang in strings.strings:
            user['lang'] = lang
            self.save()
            return True
        return False

    def ban_user(self, telegram_id: int, duration: timedelta = None) -> bool:
        user = self.get_user(telegram_id)
        if user and not user.get('banned', False):
            user['banned'] = True
            user['ban_expires_at'] = (datetime.now(timezone.utc) + duration).isoformat() if duration else None
            self.end_connection(telegram_id)
            self.save()
            return True
        return False

    def unban_user(self, telegram_id: int) -> bool:
        user = self.get_user(telegram_id)
        if user and user.get('banned', False):
            user['banned'] = False
            user['ban_expires_at'] = None
            self.save()
            return True
        return False

    def is_banned(self, telegram_id: int) -> bool:
        user = self.get_user(telegram_id)
        if not user:
            return False
        if user.get('banned', False):
            ban_expires_at = user.get('ban_expires_at')
            if ban_expires_at:
                try:
                    expiry = datetime.fromisoformat(ban_expires_at)
                    if datetime.now(timezone.utc) > expiry:
                        self.unban_user(telegram_id)
                        return False
                except ValueError:
                    logger.error(f"Invalid ban_expires_at format for user {telegram_id}: {ban_expires_at}")
            return True
        return False

    def get_by_token(self, token: str) -> Any:
        for uid, u in self.data['users'].items():
            if u.get('token') == token:
                return int(uid), u
        return None, None

    def block(self, recipient: str, nickname: str, token: str):
        if not any(block["nickname"] == nickname for block in self.data['blocks'].get(recipient, [])):
            self.data['blocks'].setdefault(recipient, []).append({"nickname": nickname, "token": token})
            self.save()

    def unblock(self, recipient: str, identifier: str):
        if recipient not in self.data['blocks']:
            return
        for block in self.data['blocks'][recipient][:]:
            if block["token"] == identifier or identifier.lower() in block["nickname"].lower():
                self.data['blocks'][recipient].remove(block)
                self.save()
                return

    def is_blocked(self, recipient: str, nickname: str) -> bool:
        if recipient not in self.data['blocks']:
            return False
        return any(block["nickname"] == nickname for block in self.data['blocks'][recipient])

    def get_blocked_users(self, recipient: str) -> List[str]:
        if recipient not in self.data['blocks']:
            return []
        return [f"<code>{block['nickname']}</code> - <code>{block['token']}</code>" for block in
                self.data['blocks'][recipient]]

    def lock_type(self, user_id: str, msg_type: str) -> bool:
        user = self.data['users'].get(user_id)
        if not user:
            return False
        if msg_type == "all":
            changed = False
            for t in self.valid_types:
                if t != "text" and t != "all" and t in user.get('allowed_types', []):
                    user['allowed_types'].remove(t)
                    changed = True
            if changed:
                self.save()
            return changed
        if msg_type in self.valid_types and msg_type != "text" and msg_type != "all":
            if msg_type in user.get('allowed_types', []):
                user['allowed_types'].remove(msg_type)
                self.save()
                return True
        return False

    def unlock_type(self, user_id: str, msg_type: str) -> bool:
        user = self.data['users'].get(user_id)
        if not user:
            return False
        if msg_type == "all":
            changed = False
            for t in self.valid_types:
                if t != "text" and t != "all" and t not in user.get('allowed_types', []):
                    user['allowed_types'].append(t)
                    changed = True
            if changed:
                self.save()
            return changed
        if msg_type in self.valid_types and msg_type != "text" and msg_type != "all":
            if msg_type not in user.get('allowed_types', []):
                user['allowed_types'].append(msg_type)
                self.save()
                return True
        return False

    def get_allowed_types(self, user_id: str) -> List[str]:
        user = self.data['users'].get(user_id)
        return user.get('allowed_types', ["text"]) if user else ["text"]

    def start_connection(self, user_id: int, target_id: int):
        # End existing connections for both users
        self.end_connection(user_id)
        self.end_connection(target_id)
        # Start new connection
        self.data['connections'][str(user_id)] = {
            "target_id": str(target_id),
            "message_count": 0,
            "started_at": datetime.now(timezone.utc).isoformat()
        }
        self.data['connections'][str(target_id)] = {
            "target_id": str(user_id),
            "message_count": 0,
            "started_at": datetime.now(timezone.utc).isoformat()
        }
        self.update_last_activity(user_id)
        self.update_last_activity(target_id)
        self.save()
        # Check for multiple connections
        user_connections = [k for k, v in self.data['connections'].items() if k == str(user_id) or v['target_id'] == str(user_id)]
        if len(user_connections) > 1:
            logger.warning(f"Multiple connections detected for user {user_id}: {user_connections}")
        target_connections = [k for k, v in self.data['connections'].items() if k == str(target_id) or v['target_id'] == str(target_id)]
        if len(target_connections) > 1:
            logger.warning(f"Multiple connections detected for target {target_id}: {target_connections}")

    def end_connection(self, user_id: int):
        # Remove user as initiator
        if str(user_id) in self.data['connections']:
            del self.data['connections'][str(user_id)]
        # Remove user as target in any other connections
        for uid, conn in list(self.data['connections'].items()):
            if conn['target_id'] == str(user_id):
                del self.data['connections'][uid]
        self.save()

    def get_connection(self, user_id: int) -> Dict[str, Any]:
        return self.data['connections'].get(str(user_id))

    def increment_message_count(self, user_id: int):
        conn = self.get_connection(user_id)
        if conn:
            conn['message_count'] = conn.get('message_count', 0) + 1
            # Increment for target as well to ensure symmetry
            target_conn = self.get_connection(int(conn['target_id']))
            if target_conn:
                target_conn['message_count'] = target_conn.get('message_count', 0) + 1
            self.save()

    def get_message_count(self, user_id: int) -> int:
        conn = self.get_connection(user_id)
        return conn.get('message_count', 0) if conn else 0

    def add_message_timestamp(self, user_id: int, target_id: int):
        user = self.get_user(user_id)
        if not user:
            return
        user['message_timestamps'].setdefault(str(target_id), []).append(datetime.now(timezone.utc).isoformat())
        # Clean up timestamps older than 1 minute
        now = datetime.now(timezone.utc)
        user['message_timestamps'][str(target_id)] = [
            ts for ts in user['message_timestamps'][str(target_id)]
            if (now - datetime.fromisoformat(ts)).total_seconds() <= 60
        ]
        self.save()

    def get_message_count_in_window(self, user_id: int, target_id: int) -> int:
        user = self.get_user(user_id)
        if not user or str(target_id) not in user['message_timestamps']:
            return 0
        return len(user['message_timestamps'][str(target_id)])

store = JSONStore(DATA_FILE)

def generate_token(user_id: int) -> str:
    salt = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    return random.choice(string.ascii_letters) + salt

def generate_nickname() -> str:
    first = random.choice(FIRST_PARTS) if FIRST_PARTS else "Anon"
    second = random.choice(SECOND_PARTS) if SECOND_PARTS else "User"
    return f"{first} {second}"

app = Client("anon_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ------------------ Scheduler for Inactivity Check ------------------
scheduler = AsyncIOScheduler()

async def check_inactive_users():
    inactive_users = store.get_inactive_users(timeout_minutes=5)
    for user in inactive_users:
        uid = user['user_id']
        user_data = user['user_data']
        conn = store.get_connection(uid)
        if not conn:
            continue
        target_id = int(conn['target_id'])
        target = store.get_user(target_id)
        if not target:
            store.end_connection(uid)
            logger.info(f"User {uid} disconnected from {target_id} due to target account not found")
            continue
        if target.get('banned', False) or store.is_blocked(str(uid), target['nickname']) or store.is_blocked(str(target_id), user_data['nickname']):
            store.end_connection(uid)
            logger.info(f"User {uid} disconnected from {target_id} due to ban or block")
            continue
        try:
            await app.get_chat(target_id)
            # Notify both users if messages were sent
            message_count = store.get_message_count(uid)
            if message_count > 0:
                user_lang = store.get_user_language(uid)
                target_lang = store.get_user_language(target_id)
                user_message_text = strings.strings.get(user_lang, strings.strings['en'])['inactivity_disconnect'].format(
                    nickname=target['nickname'])
                target_message_text = strings.strings.get(target_lang, strings.strings['en'])['inactivity_disconnect'].format(
                    nickname=user_data['nickname'])
                await app.send_message(
                    uid,
                    user_message_text,
                    parse_mode=ParseMode.HTML
                )
                await app.send_message(
                    target_id,
                    target_message_text,
                    parse_mode=ParseMode.HTML
                )
            store.end_connection(uid)
            logger.info(
                f"User {uid} ({user_data['nickname']}) and {target_id} ({target['nickname']}) disconnected due to 5 minutes of inactivity")
        except UserIsBlocked:
            store.end_connection(uid)
            logger.info(f"User {uid} disconnected from {target_id} due to UserIsBlocked")
        except InputUserDeactivated:
            store.end_connection(uid)
            logger.info(f"User {uid} disconnected from {target_id} due to InputUserDeactivated")
        except Exception as e:
            logger.error(f"Failed to notify user {uid} or {target_id} of disconnection: {type(e).__name__}: {e}")

async def can_connect(client: Client, user_id: int, target_id: int) -> tuple[bool, str]:
    """Check if a connection can be established, returning (success, reason)."""
    user = store.get_user(user_id)
    target = store.get_user(target_id)
    if not user or not target:
        return False, "invalid_peer"
    if store.is_banned(target_id):
        return False, "banned"
    if store.is_blocked(str(target_id), user['nickname']):
        return False, "blocked"
    if store.is_blocked(str(user_id), target['nickname']):
        return False, "self_blocked"
    try:
        await client.get_chat(target_id)
        return True, ""
    except UserIsBlocked:
        return False, "blocked"
    except InputUserDeactivated:
        return False, "deactivated"
    except PeerIdInvalid:
        return False, "invalid_peer"
    except Exception as e:
        logger.error(f"Failed to check bot status for {target_id}: {type(e).__name__}: {e}")
        return False, "invalid_peer"

@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client: Client, message: Message):
    user = message.from_user
    uid = user.id
    logger.info(f"Handling /start command from user ID: {uid}, Username: {user.username or 'None'}")
    if store.is_banned(uid):
        await message.reply(await strings.gstr("banned", message), parse_mode=ParseMode.HTML)
        return

    user_data = store.get_user(uid)
    if not user_data:
        token = generate_token(uid)
        nickname = generate_nickname()
        store.add_user(uid, token, nickname)
        logger.info(f"New user registered - ID: {uid}, Nickname: {nickname}, Token: {token}")
        user_data = store.get_user(uid)

    args = message.text.split()
    if len(args) == 2:
        token = args[1]
        target_id, target_data = store.get_by_token(token)
        if target_data:
            if uid == target_id:
                logger.info(f"User {uid} attempted to start connection with their own token: {token}")
                await message.reply(
                    (await strings.gstr("start_self_connect", message))
                    .format(bot_username=client.me.username, token=user_data['token'], nickname=user_data['nickname']),
                    parse_mode=ParseMode.HTML
                )
                return
            try:
                can_connect_result, reason = await can_connect(client, uid, target_id)
                if not can_connect_result:
                    logger.warning(f"Connection blocked or invalid for user {uid} to target {target_id}, reason: {reason}")
                    if reason == "banned":
                        await message.reply(
                            (await strings.gstr("start_connection_failed_frozen", message)).format(
                                nickname=target_data['nickname']),
                            parse_mode=ParseMode.HTML
                        )
                    elif reason == "self_blocked":
                        await message.reply(
                            (await strings.gstr("start_self_blocked", message)).format(
                                nickname=target_data['nickname']),
                            parse_mode=ParseMode.HTML
                        )
                    elif reason == "deactivated":
                        await message.reply(
                            (await strings.gstr("start_deactivated", message)).format(
                                nickname=target_data['nickname']),
                            parse_mode=ParseMode.HTML
                        )
                    else:
                        await message.reply(await strings.gstr("start_blocked", message), parse_mode=ParseMode.HTML)
                    return
                old_connection = store.get_connection(uid)
                store.start_connection(uid, target_id)
                logger.info(
                    f"User {uid} established connection with target ID: {target_id}, Nickname: {target_data['nickname']}")
                reply_text = (await strings.gstr("start_connection_established", message)).format(
                    nickname=target_data['nickname'])
                if old_connection and old_connection['target_id'] != str(target_id):
                    old_user = store.get_user(int(old_connection['target_id']))
                    if old_user:
                        reply_text += "\n" + (await strings.gstr("start_connection_switched", message)).format(
                            old_nickname=old_user['nickname'])
                await message.reply(reply_text, parse_mode=ParseMode.HTML)
            except (UserIsBlocked, InputUserDeactivated) as e:
                logger.warning(f"Connection failed for user {uid} to {target_id}: {type(e).__name__}")
                await message.reply(
                    (await strings.gstr("start_deactivated" if isinstance(e, InputUserDeactivated) else "start_blocked", message)).format(
                        nickname=target_data['nickname']),
                    parse_mode=ParseMode.HTML
                )
        else:
            logger.warning(f"User {uid} attempted to use invalid/expired token: {token}")
            await message.reply(await strings.gstr("start_invalid_token", message), parse_mode=ParseMode.HTML)
        return

    logger.info(f"User {uid} already registered, returning existing link")
    await message.reply(
        (await strings.gstr("start_no_token", message))
        .format(bot_username=client.me.username, token=user_data['token'], nickname=user_data['nickname']),
        parse_mode=ParseMode.HTML
    )

@app.on_message(filters.command("disconnect") & filters.private)
async def disconnect_cmd(client: Client, message: Message):
    uid = message.from_user.id
    if store.is_banned(uid):
        await message.reply(await strings.gstr("banned", message), parse_mode=ParseMode.HTML)
        return
    user = store.get_user(uid)
    if not user:
        logger.warning(f"Unauthorized disconnect attempt from user ID: {uid}")
        await message.reply(await strings.gstr("disconnect_no_user", message), parse_mode=ParseMode.HTML)
        return
    conn = store.get_connection(uid)
    if not conn:
        logger.info(f"User {uid} attempted to disconnect with no active connection")
        await message.reply(await strings.gstr("disconnect_no_connection", message), parse_mode=ParseMode.HTML)
        return
    target_id = int(conn['target_id'])
    target = store.get_user(target_id)
    if not target:
        store.end_connection(uid)
        logger.info(f"User {uid} disconnected from invalid target ID: {target_id}")
        await message.reply(await strings.gstr("disconnect_success", message).format(nickname="пользователем"),
                            parse_mode=ParseMode.HTML)
        return

    # Notify user A (initiator)
    try:
        await message.reply(
            (await strings.gstr("disconnect_success", message)).format(nickname=target['nickname']),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Failed to notify user {uid} of disconnection: {type(e).__name__}: {e}")

    # Notify user B (target) only if messages were sent and neither user blocked the other
    message_count = store.get_message_count(uid)
    if message_count > 0 and not (store.is_blocked(str(uid), target['nickname']) or store.is_blocked(str(target_id), user['nickname'])):
        try:
            await app.get_chat(target_id)
            target_message_text = await strings.gstr("disconnect_by_partner", user_id=target_id)
            target_message_text = target_message_text.format(nickname=user['nickname'])
            await app.send_message(
                target_id,
                target_message_text,
                parse_mode=ParseMode.HTML
            )
            logger.info(f"Notified target {target_id} ({target['nickname']}) of disconnection by {uid} ({user['nickname']})")
        except UserIsBlocked:
            logger.info(f"Could not notify target {target_id} of disconnection: UserIsBlocked")
        except InputUserDeactivated:
            logger.info(f"Could not notify target {target_id} of disconnection: InputUserDeactivated")
        except Exception as e:
            logger.error(f"Failed to notify target {target_id} of disconnection: {type(e).__name__}: {e}")

    # Terminate connections
    store.end_connection(uid)
    store.update_last_activity(uid)
    logger.info(f"User {uid} disconnected from target ID: {target_id}")

@app.on_message(filters.command("locktypes") & filters.private)
async def locktypes_cmd(client: Client, message: Message):
    uid = message.from_user.id
    if store.is_banned(uid):
        await message.reply(await strings.gstr("banned", message), parse_mode=ParseMode.HTML)
        return
    user = store.get_user(uid)
    if not user:
        logger.warning(f"Unauthorized locktypes attempt from user ID: {uid}")
        await message.reply(await strings.gstr("locktypes_no_user", message), parse_mode=ParseMode.HTML)
        return
    user_allowed_types = store.get_allowed_types(str(uid))
    types_display = []
    valid_types = [t for t in store.valid_types if t != "text"]
    for msg_type in valid_types:
        status = "✅" if msg_type in user_allowed_types else "🚫"
        types_display.append(f"- {msg_type} {status}")

    blocked_types = [t for t in valid_types if t != "all" and t not in user_allowed_types]
    unblocked_types = [t for t in valid_types if t != "all" and t in user_allowed_types]

    blocked_str = f"\n\n🚫 {(await strings.gstr('locktypes_blocked', message)).format(types=', '.join(blocked_types))}" if blocked_types else ""
    unblocked_str = f"\n\n✅ {(await strings.gstr('locktypes_unblocked', message)).format(types=', '.join(unblocked_types))}" if unblocked_types else ""

    response = (await strings.gstr("locktypes_response", message)).format(
        types_list="\n".join(types_display),
        blocked_types=blocked_str,
        unblocked_types=unblocked_str
    )
    await message.reply(response, parse_mode=ParseMode.HTML)
    logger.info(f"User {uid} requested lock types")

@app.on_message(filters.command("lock") & filters.private)
async def lock_cmd(client: Client, message: Message):
    uid = message.from_user.id
    if store.is_banned(uid):
        await message.reply(await strings.gstr("banned", message), parse_mode=ParseMode.HTML)
        return
    user = store.get_user(uid)
    if not user:
        logger.warning(f"Unauthorized lock attempt from user ID: {uid}")
        await message.reply(await strings.gstr("lock_no_user", message), parse_mode=ParseMode.HTML)
        return
    args = message.text.split()[1:]
    if not args:
        await message.reply(await strings.gstr("lock_no_args", message), parse_mode=ParseMode.HTML)
        return
    locked_types = []
    invalid_types = []
    if "-all" in args:
        if store.lock_type(str(uid), "all"):
            locked_types.append("all")
        args = [arg for arg in args if arg != "-all"]
    for msg_type in args:
        msg_type = msg_type.lstrip('-').lower()
        if msg_type == "text":
            invalid_types.append(msg_type)
        elif store.lock_type(str(uid), msg_type):
            if msg_type not in locked_types:
                locked_types.append(msg_type)
        else:
            invalid_types.append(msg_type)
    response = ""
    if locked_types:
        response += (await strings.gstr("lock_success", message)).format(types=', '.join(locked_types)) + "\n"
    if invalid_types:
        response += (await strings.gstr("lock_invalid_types", message)).format(types=', '.join(invalid_types))
    await message.reply(response or await strings.gstr("lock_no_args", message), parse_mode=ParseMode.HTML)
    logger.info(f"User {uid} locked types: {locked_types}, ignored: {invalid_types}")

@app.on_message(filters.command("unlock") & filters.private)
async def unlock_cmd(client: Client, message: Message):
    uid = message.from_user.id
    if store.is_banned(uid):
        await message.reply(await strings.gstr("banned", message), parse_mode=ParseMode.HTML)
        return
    user = store.get_user(uid)
    if not user:
        logger.warning(f"Unauthorized unlock attempt from user ID: {uid}")
        await message.reply(await strings.gstr("unlock_no_user", message), parse_mode=ParseMode.HTML)
        return
    args = message.text.split()[1:]
    if not args:
        await message.reply(await strings.gstr("unlock_no_args", message), parse_mode=ParseMode.HTML)
        return
    unlocked_types = []
    invalid_types = []
    if "-all" in args:
        if store.unlock_type(str(uid), "all"):
            unlocked_types.append("all")
        args = [arg for arg in args if arg != "-all"]
    for msg_type in args:
        msg_type = msg_type.lstrip('-').lower()
        if msg_type == "text":
            invalid_types.append(msg_type)
        elif store.unlock_type(str(uid), msg_type):
            if msg_type not in unlocked_types:
                unlocked_types.append(msg_type)
        else:
            invalid_types.append(msg_type)
    response = ""
    if unlocked_types:
        response += (await strings.gstr("unlock_success", message)).format(types=', '.join(unlocked_types)) + "\n"
    if invalid_types:
        response += (await strings.gstr("unlock_invalid_types", message)).format(types=', '.join(invalid_types))
    await message.reply(response or await strings.gstr("unlock_no_args", message), parse_mode=ParseMode.HTML)
    logger.info(f"User {uid} unlocked types: {unlocked_types}, ignored: {invalid_types}")

@app.on_message(filters.command("blocked") & filters.private)
async def blocked_cmd(client: Client, message: Message):
    uid = message.from_user.id
    if store.is_banned(uid):
        await message.reply(await strings.gstr("banned", message), parse_mode=ParseMode.HTML)
        return
    user = store.get_user(uid)
    if not user:
        logger.warning(f"Unauthorized blocked list attempt from user ID: {uid}")
        await message.reply(await strings.gstr("blocked_no_user", message), parse_mode=ParseMode.HTML)
        return
    if store.get_connection(uid):
        logger.info(f"User {uid} requested blocked users list with active connection, terminating connection")
        store.end_connection(uid)
    blocked_users = store.get_blocked_users(str(uid))
    if not blocked_users:
        await message.reply(await strings.gstr("blocked_none", message), parse_mode=ParseMode.HTML)
    else:
        await message.reply(
            (await strings.gstr("blocked_list", message)).format(users=', '.join(blocked_users)),
            parse_mode=ParseMode.HTML
        )
    logger.info(f"User {uid} requested blocked users list: {blocked_users}")

@app.on_message(filters.command("lang") & filters.private)
async def lang_cmd(client: Client, message: Message):
    uid = message.from_user.id
    if store.is_banned(uid):
        await message.reply(await strings.gstr("banned", message), parse_mode=ParseMode.HTML)
        return
    user = store.get_user(uid)
    if not user:
        logger.warning(f"Unauthorized language change attempt from user ID: {uid}")
        await message.reply(await strings.gstr("lang_no_user", message), parse_mode=ParseMode.HTML)
        return
    args = message.text.split()[1:]
    if not args:
        await message.reply(await strings.gstr("lang_no_args", message), parse_mode=ParseMode.HTML)
        return
    lang = args[0].lower()
    if lang not in strings.strings:
        await message.reply(
            (await strings.gstr("lang_invalid", message)).format(languages=', '.join(strings.strings.keys())),
            parse_mode=ParseMode.HTML
        )
        return
    if store.set_user_language(uid, lang):
        logger.info(f"User {uid} changed language to {lang}")
        await message.reply(
            (await strings.gstr("lang_success", message)).format(language=lang),
            parse_mode=ParseMode.HTML
        )
    else:
        await message.reply(await strings.gstr("lang_invalid", message), parse_mode=ParseMode.HTML)

@app.on_message(filters.command("ban") & filters.private)
async def ban_cmd(client: Client, message: Message):
    uid = message.from_user.id
    if store.is_banned(uid):
        await message.reply(await strings.gstr("banned", message), parse_mode=ParseMode.HTML)
        return
    if uid != OWNER_ID:
        logger.warning(f"Unauthorized ban attempt from user ID: {uid}")
        await message.reply(await strings.gstr("ban_not_owner", message), parse_mode=ParseMode.HTML)
        return
    args = message.text.split()[1:]
    if not args:
        await message.reply(await strings.gstr("ban_no_args", message), parse_mode=ParseMode.HTML)
        return
    try:
        target_id = int(args[0])
    except ValueError:
        await message.reply(await strings.gstr("ban_no_args", message), parse_mode=ParseMode.HTML)
        return
    target = store.get_user(target_id)
    if not target:
        logger.warning(f"User {uid} attempted to ban invalid user ID: {target_id}")
        await message.reply(
            (await strings.gstr("ban_invalid_user", message)).format(user_id=target_id),
            parse_mode=ParseMode.HTML
        )
        return
    if store.is_banned(target_id):
        logger.info(f"User {uid} attempted to ban already banned user ID: {target_id}")
        await message.reply(
            (await strings.gstr("ban_already_banned", message)).format(user_id=target_id),
            parse_mode=ParseMode.HTML
        )
        return
    store.ban_user(target_id)
    logger.info(f"User {target_id} banned by owner {uid}")
    await message.reply(
        (await strings.gstr("ban_success", message)).format(user_id=target_id),
        parse_mode=ParseMode.HTML
    )

@app.on_message(filters.command("unban") & filters.private)
async def unban_cmd(client: Client, message: Message):
    uid = message.from_user.id
    if store.is_banned(uid):
        await message.reply(await strings.gstr("banned", message), parse_mode=ParseMode.HTML)
        return
    if uid != OWNER_ID:
        logger.warning(f"Unauthorized unban attempt from user ID: {uid}")
        await message.reply(await strings.gstr("unban_not_owner", message), parse_mode=ParseMode.HTML)
        return
    args = message.text.split()[1:]
    if not args:
        await message.reply(await strings.gstr("unban_no_args", message), parse_mode=ParseMode.HTML)
        return
    try:
        target_id = int(args[0])
    except ValueError:
        await message.reply(await strings.gstr("unban_no_args", message), parse_mode=ParseMode.HTML)
        return
    target = store.get_user(target_id)
    if not target:
        logger.warning(f"User {uid} attempted to unban invalid user ID: {target_id}")
        await message.reply(
            (await strings.gstr("ban_invalid_user", message)).format(user_id=target_id),
            parse_mode=ParseMode.HTML
        )
        return
    if not store.is_banned(target_id):
        logger.info(f"User {uid} attempted to unban non-banned user ID: {target_id}")
        await message.reply(
            (await strings.gstr("unban_not_banned", message)).format(user_id=target_id),
            parse_mode=ParseMode.HTML
        )
        return
    store.unban_user(target_id)
    logger.info(f"User {target_id} unbanned by owner {uid}")
    await message.reply(
        (await strings.gstr("unban_success", message)).format(user_id=target_id),
        parse_mode=ParseMode.HTML
    )

@app.on_message(
    filters.private
    & ~filters.command(
        ["start", "disconnect", "locktypes", "lock", "unlock", "blocked", "block", "unblock", "report", "ban", "unban", "lang"]
    )
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
    )
)
async def anonymous_handler(client: Client, message: Message):
    uid = message.from_user.id
    user = store.get_user(uid)
    if not user:
        logger.warning(f"Unauthorized message attempt from user ID: {uid}")
        await message.reply(await strings.gstr("anonymous_no_user", message), parse_mode=ParseMode.HTML)
        return
    if store.is_banned(uid):
        await message.reply(await strings.gstr("banned", message), parse_mode=ParseMode.HTML)
        return
    store.update_last_activity(uid)

    # Determine message type
    msg_type = "text"
    is_link = False
    if message.text or message.caption:
        msg_type = "text"
        # Check for links in text or caption
        entities = message.entities or message.caption_entities or []
        if any(e.type in [MessageEntityType.URL, MessageEntityType.TEXT_LINK] for e in entities):
            is_link = True
            msg_type = "link"
    elif message.audio:
        msg_type = "audio"
    elif message.photo:
        msg_type = "photo"
    elif message.document:
        msg_type = "document"
    elif message.forward_origin:
        msg_type = "forward"
    elif message.animation:
        msg_type = "gif"
    elif message.location:
        msg_type = "location"
    elif message.poll:
        msg_type = "poll"
    elif message.video:
        msg_type = "video"
    elif message.video_note:
        msg_type = "videonote"
    elif message.voice:
        msg_type = "voice"
    elif message.media_group_id:
        msg_type = "album"

    logger.info(f"Processing message type '{msg_type}' from user {uid}")

    # Safety check
    if msg_type not in store.valid_types:
        logger.warning(f"Unexpected message type '{msg_type}' sent by user {uid}")
        await message.reply(await strings.gstr("anonymous_unsupported_type", message), parse_mode=ParseMode.HTML)
        return

    target_id = None
    old_connection = store.get_connection(uid)
    old_nickname = None
    if old_connection:
        old_user = store.get_user(int(old_connection['target_id']))
        old_nickname = old_user['nickname'] if old_user else None

    if message.reply_to_message:
        lines = (message.reply_to_message.caption or message.reply_to_message.text or "")
        logger.debug(f"Reply message content: {lines!r}")
        sender_nickname = None
        if lines.startswith("✅"):
            lines_parts = lines.split('\n')
            for line in lines_parts:
                if line.startswith("✅"):
                    parts = line.split(" ")
                    if len(parts) >= 4:
                        sender_nickname = " ".join(parts[3:]).replace('<b>', '').replace('</b>', '')
                        break
        elif '–– ' in lines:
            sender_nickname_block = lines.split('–– ')[-1]
            sender_nickname_line = sender_nickname_block.split('\n')[0].strip()
            sender_nickname = sender_nickname_line.replace('<b>', '').replace('</b>', '')
        logger.debug(f"Extracted sender_nickname: {sender_nickname!r}")
        if sender_nickname:
            for user_id, u in store.data['users'].items():
                if u['nickname'].strip() == sender_nickname:
                    target_id = int(user_id)
                    break
        if not target_id or target_id == uid:
            logger.warning(f"Reply target not found or self-reply for nickname: {sender_nickname!r}, user ID: {uid}")
            await message.reply(await strings.gstr("anonymous_reply_not_found", message), parse_mode=ParseMode.HTML)
            return
        try:
            can_connect_result, reason = await can_connect(client, uid, target_id)
            if not can_connect_result:
                logger.warning(f"Connection blocked or invalid for user {uid} to target {target_id}, reason: {reason}")
                if reason == "banned":
                    target_data = store.get_user(target_id)
                    await message.reply(
                        (await strings.gstr("start_connection_failed_frozen", message)).format(
                            nickname=target_data['nickname']),
                        parse_mode=ParseMode.HTML
                    )
                elif reason == "self_blocked":
                    target_data = store.get_user(target_id)
                    await message.reply(
                        (await strings.gstr("start_self_blocked", message)).format(
                            nickname=target_data['nickname']),
                        parse_mode=ParseMode.HTML
                    )
                elif reason == "deactivated":
                    target_data = store.get_user(target_id)
                    await message.reply(
                        (await strings.gstr("start_deactivated", message)).format(
                            nickname=target_data['nickname']),
                        parse_mode=ParseMode.HTML
                    )
                else:
                    await message.reply(await strings.gstr("anonymous_blocked", message), parse_mode=ParseMode.HTML)
                return
            # Switch connection only if replying to a different user
            if old_connection and old_connection['target_id'] != str(target_id):
                old_target_id = int(old_connection['target_id'])
                old_target = store.get_user(old_target_id)
                if old_target and store.get_message_count(uid) > 0:
                    try:
                        await app.get_chat(old_target_id)
                        target_message_text = await strings.gstr("disconnect_by_partner", user_id=old_target_id)
                        target_message_text = target_message_text.format(nickname=user['nickname'])
                        await app.send_message(
                            old_target_id,
                            target_message_text,
                            parse_mode=ParseMode.HTML
                        )
                        logger.info(f"Notified target {old_target_id} ({old_target['nickname']}) of disconnection by {uid} ({user['nickname']}) due to reply switch")
                    except (UserIsBlocked, InputUserDeactivated):
                        logger.info(f"Could not notify target {old_target_id} of disconnection: {type(e).__name__}")
                    except Exception as e:
                        logger.error(f"Failed to notify target {old_target_id} of disconnection: {type(e).__name__}: {e}")
                store.start_connection(uid, target_id)
                logger.info(f"User {uid} switched connection to target ID: {target_id}, Nickname: {sender_nickname}")
            elif not old_connection:
                store.start_connection(uid, target_id)
                logger.info(f"User {uid} established connection with target ID: {target_id}, Nickname: {sender_nickname}")
        except (UserIsBlocked, InputUserDeactivated) as e:
            logger.warning(f"Connection failed for user {uid} to {target_id}: {type(e).__name__}")
            await message.reply(
                (await strings.gstr("start_deactivated" if isinstance(e, InputUserDeactivated) else "start_blocked", message)).format(
                    nickname=sender_nickname),
                parse_mode=ParseMode.HTML
            )
            return
    else:
        conn = store.get_connection(uid)
        if not conn:
            logger.warning(f"Unauthorized message attempt from user ID: {uid}")
            await message.reply(await strings.gstr("anonymous_no_connection", message), parse_mode=ParseMode.HTML)
            return
        target_id = int(conn['target_id'])
        try:
            can_connect_result, reason = await can_connect(client, uid, target_id)
            if not can_connect_result:
                logger.warning(f"Connection blocked or invalid for user {uid} to target {target_id}, reason: {reason}")
                if reason == "banned":
                    target_data = store.get_user(target_id)
                    store.end_connection(uid)
                    store.update_last_activity(uid)
                    await message.reply(
                        (await strings.gstr("start_connection_failed_frozen", message)).format(
                            nickname=target_data['nickname']),
                        parse_mode=ParseMode.HTML
                    )
                elif reason == "self_blocked":
                    target_data = store.get_user(target_id)
                    store.end_connection(uid)
                    store.update_last_activity(uid)
                    await message.reply(
                        (await strings.gstr("start_self_blocked", message)).format(
                            nickname=target_data['nickname']),
                        parse_mode=ParseMode.HTML
                    )
                elif reason == "deactivated":
                    target_data = store.get_user(target_id)
                    store.end_connection(uid)
                    store.update_last_activity(uid)
                    await message.reply(
                        (await strings.gstr("start_deactivated", message)).format(
                            nickname=target_data['nickname']),
                        parse_mode=ParseMode.HTML
                    )
                else:
                    await message.reply(await strings.gstr("anonymous_blocked", message), parse_mode=ParseMode.HTML)
                return
        except (UserIsBlocked, InputUserDeactivated) as e:
            logger.warning(f"Connection failed for user {uid} to {target_id}: {type(e).__name__}")
            target_data = store.get_user(target_id)
            store.end_connection(uid)
            store.update_last_activity(uid)
            await message.reply(
                (await strings.gstr("start_deactivated" if isinstance(e, InputUserDeactivated) else "anonymous_blocked", message)).format(
                    nickname=target_data['nickname'] if target_data else "пользователем"),
                parse_mode=ParseMode.HTML
            )
            return

    target = store.get_user(target_id)
    if not target:
        logger.warning(f"Invalid target - Target ID: {target_id}")
        store.end_connection(uid)
        store.update_last_activity(uid)
        await message.reply(await strings.gstr("anonymous_target_not_found", message), parse_mode=ParseMode.HTML)
        return

    if msg_type not in store.get_allowed_types(str(target_id)):
        logger.info(f"Message type {msg_type} not allowed for {target_id}")
        await message.reply(
            (await strings.gstr("anonymous_type_blocked", message)).format(type=msg_type),
            parse_mode=ParseMode.HTML
        )
        return

    # Anti-spam check: 60 messages per minute
    store.add_message_timestamp(uid, target_id)
    message_count = store.get_message_count_in_window(uid, target_id)
    if message_count > 60:
        ban_duration = timedelta(days=1)
        store.ban_user(uid, ban_duration)
        logger.warning(f"User {uid} banned for 1 day due to exceeding 60 messages per minute to {target_id}")
        try:
            report_text = (await strings.gstr("spam_report", message)).format(
                user_id=uid,
                nickname=user['nickname'],
                target_id=target_id,
                target_nickname=target['nickname'],
                message_count=message_count
            )
            await client.send_message(MODERATION_CHAT, report_text, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Failed to send spam report for user {uid}: {type(e).__name__}: {e}")
        await message.reply(
            (await strings.gstr("spam_banned", message)).format(duration="1 day"),
            parse_mode=ParseMode.HTML
        )
        return

    try:
        original_caption = message.caption or message.text or ""
        caption = (await strings.gstr("anonymous_caption", message)).format(original=original_caption,
                                                                            nickname=user['nickname'])
        target_conn = store.get_connection(target_id)
        if target_conn and int(target_conn['target_id']) != uid:
            target_user = store.get_user(int(target_conn['target_id']))
            target_nickname = target_user['nickname'] if target_user else "Unknown"
            caption += "\n\n" + (await strings.gstr("anonymous_switch_info", message)).format(
                sender_nickname=user['nickname'], current_nickname=target_nickname
            )
        elif not target_conn:
            caption += "\n\n" + (await strings.gstr("anonymous_no_connection_info", message)).format(
                nickname=target['nickname'])

        if msg_type == "text" or msg_type == "link":
            await client.send_message(target_id, caption, parse_mode=ParseMode.HTML)
        elif msg_type == "audio":
            await client.send_audio(target_id, message.audio.file_id, caption=caption, parse_mode=ParseMode.HTML)
        elif msg_type == "photo":
            await client.send_photo(target_id, message.photo.file_id, caption=caption, parse_mode=ParseMode.HTML)
        elif msg_type == "document":
            await client.send_document(target_id, message.document.file_id, caption=caption, parse_mode=ParseMode.HTML)
        elif msg_type == "forward":
            await message.forward(target_id)
            await client.send_message(target_id, caption, parse_mode=ParseMode.HTML)
        elif msg_type == "gif":
            await client.send_animation(target_id, message.animation.file_id, caption=caption,
                                        parse_mode=ParseMode.HTML)
        elif msg_type == "location":
            await client.send_location(target_id, message.location.latitude, message.location.longitude,
                                       caption=caption, parse_mode=ParseMode.HTML)
        elif msg_type == "poll":
            await client.send_poll(target_id, message.poll.question, [option.text for option in message.poll.options],
                                   caption=caption, parse_mode=ParseMode.HTML)
        elif msg_type == "video":
            await client.send_video(target_id, message.video.file_id, caption=caption, parse_mode=ParseMode.HTML)
        elif msg_type == "videonote":
            await client.send_video_note(target_id, message.video_note.file_id)
            await client.send_message(target_id, caption, parse_mode=ParseMode.HTML)
        elif msg_type == "voice":
            await client.send_voice(target_id, message.voice.file_id, caption=caption, parse_mode=ParseMode.HTML)
        elif msg_type == "album":
            media_group = [m for m in message.media_group if m.media_group_id == message.media_group_id]
            for media in media_group:
                media_caption = (await strings.gstr("anonymous_caption", message)).format(
                    original=media.caption or '', nickname=user['nickname']
                )
                if target_conn and int(target_conn['target_id']) != uid:
                    target_user = store.get_user(int(target_conn['target_id']))
                    target_nickname = target_user['nickname'] if target_user else "Unknown"
                    media_caption += "\n\n" + (await strings.gstr("anonymous_switch_info", message)).format(
                        sender_nickname=user['nickname'], current_nickname=target_nickname
                    )
                elif not target_conn:
                    media_caption += "\n\n" + (await strings.gstr("anonymous_no_connection_info", message)).format(
                        nickname=target['nickname'])
                if media.photo:
                    await client.send_photo(target_id, media.photo.file_id, caption=media_caption,
                                            parse_mode=ParseMode.HTML)
                elif media.video:
                    await client.send_video(target_id, media.video.file_id, caption=media_caption,
                                            parse_mode=ParseMode.HTML)

        store.increment_message_count(uid)
        logger.info(f"Message type {msg_type} sent from {user['nickname']} (ID: {uid}) to {target_id}")
        reply_text = (await strings.gstr("anonymous_sent", message)).format(nickname=target['nickname'])
        if message.reply_to_message and old_connection and old_connection['target_id'] != str(target_id) and old_nickname:
            reply_text += "\n" + (await strings.gstr("anonymous_switched", message)).format(
                old_nickname=old_nickname, new_nickname=target['nickname']
            )
        await message.reply(reply_text, parse_mode=ParseMode.HTML)
    except UserIsBlocked:
        logger.warning(f"Message failed: User {target_id} blocked the bot")
        store.end_connection(uid)
        store.update_last_activity(uid)
        await message.reply(await strings.gstr("anonymous_target_blocked_bot", message), parse_mode=ParseMode.HTML)
    except InputUserDeactivated:
        logger.warning(f"Message failed: User {target_id} account deactivated")
        store.end_connection(uid)
        store.update_last_activity(uid)
        await message.reply(
            (await strings.gstr("start_deactivated", message)).format(nickname=target['nickname'] if target else "пользователем"),
            parse_mode=ParseMode.HTML
        )
    except FloodWait as e:
        logger.warning(f"Message delayed due to FloodWait: {e.x} seconds")
        await asyncio.sleep(e.x)
        await message.reply(await strings.gstr("anonymous_flood_wait", message), parse_mode=ParseMode.HTML)
    except PeerIdInvalid:
        logger.error(f"Message failed: Invalid peer ID {target_id}")
        store.end_connection(uid)
        store.update_last_activity(uid)
        await message.reply(await strings.gstr("anonymous_invalid_peer", message), parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Message failed: {type(e).__name__}: {e}")
        await message.reply(await strings.gstr("anonymous_error", message), parse_mode=ParseMode.HTML)

@app.on_message(
    filters.private
    & ~filters.command(
        ["start", "disconnect", "locktypes", "lock", "unlock", "blocked", "block", "unblock", "report", "ban", "unban", "lang"]
    )
    & (
        filters.sticker
        | filters.contact
        | filters.dice
        | filters.game
        | filters.venue
        | filters.successful_payment
    )
)
async def unsupported_handler(client: Client, message: Message):
    uid = message.from_user.id
    user = store.get_user(uid)
    if not user:
        logger.warning(f"Unauthorized unsupported message attempt from user ID: {uid}")
        await message.reply(await strings.gstr("anonymous_no_user", message), parse_mode=ParseMode.HTML)
        return
    if store.is_banned(uid):
        await message.reply(await strings.gstr("banned", message), parse_mode=ParseMode.HTML)
        return
    store.update_last_activity(uid)
    logger.info(f"Received unsupported message type from user {uid}")
    await message.reply(await strings.gstr("anonymous_unsupported_type", message), parse_mode=ParseMode.HTML)

@app.on_message(filters.command("block") & filters.private)
async def block_handler(client: Client, message: Message):
    uid = message.from_user.id
    if store.is_banned(uid):
        await message.reply(await strings.gstr("banned", message), parse_mode=ParseMode.HTML)
        return
    user = store.get_user(uid)
    if not user:
        logger.warning(f"Unauthorized block attempt from user ID: {uid}")
        await message.reply(await strings.gstr("block_no_user", message), parse_mode=ParseMode.HTML)
        return
    args = message.text.split()
    recipient = str(uid)

    if store.get_connection(uid):
        store.end_connection(uid)

    if len(args) == 2 and not message.reply_to_message:
        token = args[1]
        target_id, target_data = store.get_by_token(token)
        if not target_data:
            logger.warning(f"User {uid} attempted to block invalid token: {token}")
            await message.reply(
                (await strings.gstr("block_invalid_token", message)).format(token=token),
                parse_mode=ParseMode.HTML
            )
            return
        if target_id == uid:
            logger.warning(f"User {uid} attempted to block their own token: {token}")
            await message.reply(await strings.gstr("block_self", message), parse_mode=ParseMode.HTML)
            return
        try:
            await client.get_chat(target_id)
            if store.is_blocked(recipient, target_data['nickname']):
                logger.info(
                    f"User {recipient} attempted to block already blocked nickname: {target_data['nickname']} (Token: {token})")
                await message.reply(
                    (await strings.gstr("block_already_blocked", message)).format(nickname=target_data['nickname']),
                    parse_mode=ParseMode.HTML
                )
                return
            store.block(recipient, target_data['nickname'], token)
            logger.info(f"User {recipient} blocked nickname: {target_data['nickname']} (Token: {token})")
            await message.reply(
                (await strings.gstr("block_success", message)).format(nickname=target_data['nickname']),
                parse_mode=ParseMode.HTML
            )
        except InputUserDeactivated:
            logger.warning(f"User {uid} attempted to block deactivated user: {target_id}")
            await message.reply(
                (await strings.gstr("block_deactivated", message)).format(nickname=target_data['nickname']),
                parse_mode=ParseMode.HTML
            )
        return

    if not message.reply_to_message:
        logger.warning(f"User {uid} attempted /block without reply or token")
        await message.reply(await strings.gstr("block_no_args", message), parse_mode=ParseMode.HTML)
        return

    lines = (message.reply_to_message.caption or message.reply_to_message.text or "")
    logger.debug(f"Block command - Reply message content: {lines!r}")
    sender_nickname = None
    if lines.startswith("✅"):
        lines_parts = lines.split('\n')
        for line in lines_parts:
            if line.startswith("✅"):
                parts = line.split(" ")
                if len(parts) >= 4:
                    sender_nickname = " ".join(parts[3:]).replace('<b>', '').replace('</b>', '')
                    break
    elif '–– ' in lines:
        sender_nickname_block = lines.split('–– ')[-1]
        sender_nickname_line = sender_nickname_block.split('\n')[0].strip()
        sender_nickname = sender_nickname_line.replace('<b>', '').replace('</b>', '')
    if not sender_nickname:
        logger.warning(f"Invalid nickname for block attempt by user ID: {uid}")
        await message.reply(await strings.gstr("block_no_nickname", message), parse_mode=ParseMode.HTML)
        return
    blocked_token = None
    target_id = None
    for user_id, u in store.data['users'].items():
        if u['nickname'].strip() == sender_nickname:
            blocked_token = u['token']
            target_id = int(user_id)
            break
    if not blocked_token:
        logger.warning(f"Nickname {sender_nickname} not found in users for block attempt by user ID: {uid}")
        await message.reply(await strings.gstr("block_nickname_not_found", message), parse_mode=ParseMode.HTML)
        return
    try:
        await client.get_chat(target_id)
        if store.is_blocked(recipient, sender_nickname):
            logger.info(
                f"User {recipient} attempted to block already blocked nickname: {sender_nickname} (Token: {blocked_token})")
            await message.reply(
                (await strings.gstr("block_already_blocked", message)).format(nickname=sender_nickname),
                parse_mode=ParseMode.HTML
            )
            return
        store.block(recipient, sender_nickname, blocked_token)
        logger.info(f"User {recipient} blocked nickname: {sender_nickname} (Token: {blocked_token})")
        await message.reply(
            (await strings.gstr("block_success", message)).format(nickname=sender_nickname),
            parse_mode=ParseMode.HTML
        )
    except InputUserDeactivated:
        logger.warning(f"User {uid} attempted to block deactivated user: {target_id}")
        await message.reply(
            (await strings.gstr("block_deactivated", message)).format(nickname=sender_nickname),
            parse_mode=ParseMode.HTML
        )

@app.on_message(filters.command("unblock") & filters.private)
async def unblock_handler(client: Client, message: Message):
    uid = message.from_user.id
    if store.is_banned(uid):
        await message.reply(await strings.gstr("banned", message), parse_mode=ParseMode.HTML)
        return
    user = store.get_user(uid)
    if not user:
        logger.warning(f"Unauthorized unblock attempt from user ID: {uid}")
        await message.reply(await strings.gstr("unblock_no_user", message), parse_mode=ParseMode.HTML)
        return
    args = message.text.split()[1:]
    if not args:
        await message.reply(await strings.gstr("unblock_no_args", message), parse_mode=ParseMode.HTML)
        return
    identifier = args[0]
    recipient = str(message.from_user.id)
    if not store.data['blocks'].get(recipient) or not any(
            block["token"] == identifier or identifier.lower() in block["nickname"].lower()
            for block in store.data['blocks'][recipient]
    ):
        logger.warning(f"Identifier {identifier} not blocked by user ID: {uid}")
        await message.reply(
            (await strings.gstr("unblock_not_blocked", message)).format(identifier=identifier),
            parse_mode=ParseMode.HTML
        )
        return
    store.unblock(recipient, identifier)
    logger.info(f"User {recipient} unblocked identifier: {identifier}")
    await message.reply(
        (await strings.gstr("unblock_success", message)).format(identifier=identifier),
        parse_mode=ParseMode.HTML
    )

@app.on_message(filters.command("report") & filters.reply & filters.private)
async def report_handler(client: Client, message: Message):
    uid = message.from_user.id
    if store.is_banned(uid):
        await message.reply(await strings.gstr("banned", message), parse_mode=ParseMode.HTML)
        return
    user = store.get_user(uid)
    if not user:
        logger.warning(f"Unauthorized report attempt from user ID: {uid}")
        await message.reply(await strings.gstr("report_no_user", message), parse_mode=ParseMode.HTML)
        return
    replied_message = message.reply_to_message
    lines = (replied_message.caption or replied_message.text or "")
    logger.debug(f"Report command - Reply message content: {lines!r}")
    sender_nickname = None
    if lines.startswith("✅"):
        lines_parts = lines.split('\n')
        for line in lines_parts:
            if line.startswith("✅"):
                parts = line.split(" ")
                if len(parts) >= 4:
                    sender_nickname = " ".join(parts[3:]).replace('<b>', '').replace('</b>', '')
                    break
    elif '–– ' in lines:
        sender_nickname_block = lines.split('–– ')[-1]
        sender_nickname_line = sender_nickname_block.split('\n')[0].strip()
        sender_nickname = sender_nickname_line.replace('<b>', '').replace('</b>', '')
    if not sender_nickname:
        logger.warning(f"Invalid nickname for report attempt by user ID: {uid}")
        await message.reply(await strings.gstr("report_no_nickname", message), parse_mode=ParseMode.HTML)
        return
    reported_id = None
    for user_id, u in store.data['users'].items():
        if u['nickname'].strip() == sender_nickname:
            reported_id = int(user_id)
            break
    try:
        forwarded_message = await replied_message.forward(MODERATION_CHAT)
        report_text = (await strings.gstr("report_message", message)).format(
            user_id=uid,
            nickname=sender_nickname,
            reported_id=reported_id if reported_id is not None else "Unknown",
            type=replied_message.media or 'text',
            message_id=forwarded_message.id
        )
        await client.send_message(MODERATION_CHAT, report_text, parse_mode=ParseMode.HTML)
        logger.info(
            f"Report sent from user {uid} about {sender_nickname} (ID: {reported_id if reported_id is not None else 'Unknown'}), message ID: {forwarded_message.id}")
        await message.reply(await strings.gstr("report_success", message), parse_mode=ParseMode.HTML)
    except InputUserDeactivated:
        logger.warning(f"Report failed: Moderation chat {MODERATION_CHAT} is deactivated")
        await message.reply(await strings.gstr("report_deactivated", message), parse_mode=ParseMode.HTML)

# ------------------ Bot initialization ------------------
async def init_bot():
    """Start the bot and keep it running."""
    attempt = 1
    while True:
        try:
            await app.start()
            bot_info = await app.get_me()
            logger.info(
                f"🤖 Bot '{bot_info.first_name}' (ID: {bot_info.id}) started"
                + (f" after {attempt} tries" if attempt > 1 else "")
            )
            scheduler.add_job(check_inactive_users, 'interval', minutes=1)
            scheduler.start()
            break
        except Exception as e:
            logger.error(f"⚠️ Bot start failed: {type(e).__name__}: {e} | Attempt {attempt}")
            attempt += 1
            await asyncio.sleep(2)

    try:
        await idle()
    except KeyboardInterrupt:
        logger.info("💤 Stop signal received. Shutting down…")
    finally:
        if app.is_connected:
            await app.stop()
        scheduler.shutdown()
        logger.info("🛑 Bot stopped cleanly.")

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(init_bot())