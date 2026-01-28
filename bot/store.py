"""Thread-safe JSON data store with asyncio locking."""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


class JSONStore:
    """Thread-safe JSON-based data store for user data, blocks, and connections."""

    # Message content types that can be locked/unlocked
    VALID_TYPES = [
        "all",
        # Text content
        "text", "url", "email", "phone", "cashtag", "spoiler",
        # Text filters
        "emoji", "emojionly", "emojicustom", "cyrillic", "zalgo",
        # Media
        "photo", "video", "gif", "voice", "videonote", "audio", "document",
        # Stickers
        "sticker", "stickeranimated", "stickerpremium",
        # Interactive
        "location", "poll", "inline", "button", "game", "emojigame",
        # Forwards
        "forward", "forwardbot", "forwardchannel", "forwardstory", "forwarduser",
        # Other
        "externalreply",
    ]
    # Types that are always blocked (no user option)
    BLOCKED_TYPES = [
        "contact", "venue", "successful_payment"
    ]
    # Default allowed types for new users
    DEFAULT_ALLOWED = ["text", "photo", "video", "voice", "document", "emoji"]

    def __init__(self, path: str):
        self.path = path
        self._lock = asyncio.Lock()
        self._data: Dict[str, Any] = {"users": {}, "blocks": {}, "connections": {}}

        if not os.path.exists(path):
            self._save_sync()
        else:
            self._load_sync()

    def _load_sync(self) -> None:
        """Synchronously load data from file."""
        with open(self.path, 'r') as f:
            self._data = json.load(f)

    def _save_sync(self) -> None:
        """Synchronously save data to file."""
        with open(self.path, 'w') as f:
            json.dump(self._data, f, indent=2, default=str)

    async def _save(self) -> None:
        """Save data to file (must be called within lock)."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._save_sync)

    # ----- User Management -----

    async def add_user(self, telegram_id: int, token: str, nickname: str) -> None:
        """Add a new user to the store."""
        async with self._lock:
            self._data['users'][str(telegram_id)] = {
                "token": token,
                "nickname": nickname,
                "registered_at": datetime.now(timezone.utc).isoformat(),
                "allowed_types": self.DEFAULT_ALLOWED.copy(),
                "last_activity": datetime.now(timezone.utc).isoformat(),
                "lang": "en",
                "banned": False,
                "ban_expires_at": None,
                "message_timestamps": {}
            }
            await self._save()

    def get_user(self, telegram_id: int) -> Optional[Dict[str, Any]]:
        """Get user data by telegram ID (read-only, no lock needed)."""
        return self._data['users'].get(str(telegram_id))

    def get_user_language(self, telegram_id: int) -> str:
        """Get user's language preference."""
        user = self.get_user(telegram_id)
        return user.get('lang', 'en') if user else 'en'

    async def set_user_language(self, telegram_id: int, lang: str, available_langs: List[str]) -> bool:
        """Set user's language preference."""
        async with self._lock:
            user = self._data['users'].get(str(telegram_id))
            if user and lang in available_langs:
                user['lang'] = lang
                await self._save()
                return True
            return False

    async def update_last_activity(self, telegram_id: int) -> None:
        """Update user's last activity timestamp."""
        async with self._lock:
            user = self._data['users'].get(str(telegram_id))
            if user:
                user['last_activity'] = datetime.now(timezone.utc).isoformat()
                await self._save()

    def get_by_token(self, token: str) -> Tuple[Optional[int], Optional[Dict[str, Any]]]:
        """Find user by token."""
        for uid, u in self._data['users'].items():
            if u.get('token') == token:
                return int(uid), u
        return None, None

    def find_user_by_nickname(self, nickname: str) -> Optional[int]:
        """Find user ID by nickname."""
        for user_id, u in self._data['users'].items():
            if u['nickname'].strip() == nickname:
                return int(user_id)
        return None

    # ----- Ban Management -----

    async def ban_user(self, telegram_id: int, duration: Optional[timedelta] = None) -> bool:
        """Ban a user, optionally for a duration."""
        async with self._lock:
            user = self._data['users'].get(str(telegram_id))
            if user and not user.get('banned', False):
                user['banned'] = True
                user['ban_expires_at'] = (datetime.now(timezone.utc) + duration).isoformat() if duration else None
                await self._end_connection_unlocked(telegram_id)
                await self._save()
                return True
            return False

    async def unban_user(self, telegram_id: int) -> bool:
        """Unban a user."""
        async with self._lock:
            user = self._data['users'].get(str(telegram_id))
            if user and user.get('banned', False):
                user['banned'] = False
                user['ban_expires_at'] = None
                await self._save()
                return True
            return False

    def is_banned(self, telegram_id: int) -> bool:
        """Check if user is banned (auto-unbans if expired)."""
        user = self.get_user(telegram_id)
        if not user:
            return False
        if user.get('banned', False):
            ban_expires_at = user.get('ban_expires_at')
            if ban_expires_at:
                try:
                    expiry = datetime.fromisoformat(ban_expires_at)
                    if datetime.now(timezone.utc) > expiry:
                        # Note: This is a read operation that triggers a write
                        # We handle this specially to avoid deadlock
                        asyncio.create_task(self.unban_user(telegram_id))
                        return False
                except ValueError:
                    logger.error(f"Invalid ban_expires_at format for user {telegram_id}: {ban_expires_at}")
            return True
        return False

    # ----- Block Management -----

    async def block(self, recipient_id: str, nickname: str, token: str) -> None:
        """Block a user by nickname."""
        async with self._lock:
            blocks = self._data['blocks'].setdefault(recipient_id, [])
            if not any(block["nickname"] == nickname for block in blocks):
                blocks.append({"nickname": nickname, "token": token})
                await self._save()

    async def unblock(self, recipient_id: str, identifier: str) -> bool:
        """Unblock a user by token or nickname."""
        async with self._lock:
            if recipient_id not in self._data['blocks']:
                return False
            for block in self._data['blocks'][recipient_id][:]:
                if block["token"] == identifier or identifier.lower() in block["nickname"].lower():
                    self._data['blocks'][recipient_id].remove(block)
                    await self._save()
                    return True
            return False

    def is_blocked(self, recipient_id: str, nickname: str) -> bool:
        """Check if a nickname is blocked by recipient."""
        if recipient_id not in self._data['blocks']:
            return False
        return any(block["nickname"] == nickname for block in self._data['blocks'][recipient_id])

    def get_blocked_users(self, recipient_id: str) -> List[str]:
        """Get list of blocked users for display."""
        if recipient_id not in self._data['blocks']:
            return []
        return [
            f"<code>{block['nickname']}</code> - <code>{block['token']}</code>"
            for block in self._data['blocks'][recipient_id]
        ]

    def is_user_blocked(self, recipient_id: str, identifier: str) -> bool:
        """Check if identifier (token or nickname) is blocked."""
        if recipient_id not in self._data['blocks']:
            return False
        return any(
            block["token"] == identifier or identifier.lower() in block["nickname"].lower()
            for block in self._data['blocks'][recipient_id]
        )

    # ----- Message Type Locking -----

    async def lock_type(self, user_id: str, msg_type: str) -> bool:
        """Lock a message type for user."""
        async with self._lock:
            user = self._data['users'].get(user_id)
            if not user:
                return False
            if msg_type == "all":
                changed = False
                for t in self.VALID_TYPES:
                    if t != "text" and t != "all" and t in user.get('allowed_types', []):
                        user['allowed_types'].remove(t)
                        changed = True
                if changed:
                    await self._save()
                return changed
            if msg_type in self.VALID_TYPES and msg_type != "text" and msg_type != "all":
                if msg_type in user.get('allowed_types', []):
                    user['allowed_types'].remove(msg_type)
                    await self._save()
                    return True
            return False

    async def unlock_type(self, user_id: str, msg_type: str) -> bool:
        """Unlock a message type for user."""
        async with self._lock:
            user = self._data['users'].get(user_id)
            if not user:
                return False
            if msg_type == "all":
                changed = False
                for t in self.VALID_TYPES:
                    if t != "text" and t != "all" and t not in user.get('allowed_types', []):
                        user['allowed_types'].append(t)
                        changed = True
                if changed:
                    await self._save()
                return changed
            if msg_type in self.VALID_TYPES and msg_type != "text" and msg_type != "all":
                if msg_type not in user.get('allowed_types', []):
                    user['allowed_types'].append(msg_type)
                    await self._save()
                    return True
            return False

    def get_allowed_types(self, user_id: str) -> List[str]:
        """Get allowed message types for user."""
        user = self._data['users'].get(user_id)
        return user.get('allowed_types', ["text"]) if user else ["text"]

    # ----- Connection Management -----

    async def start_connection(self, user_id: int, target_id: int) -> None:
        """Start a bidirectional connection between two users."""
        async with self._lock:
            # End existing connections for both users
            await self._end_connection_unlocked(user_id)
            await self._end_connection_unlocked(target_id)
            # Start new connection
            now = datetime.now(timezone.utc).isoformat()
            self._data['connections'][str(user_id)] = {
                "target_id": str(target_id),
                "message_count": 0,
                "started_at": now
            }
            self._data['connections'][str(target_id)] = {
                "target_id": str(user_id),
                "message_count": 0,
                "started_at": now
            }
            # Update activity
            for uid in [user_id, target_id]:
                user = self._data['users'].get(str(uid))
                if user:
                    user['last_activity'] = now
            await self._save()

    async def _end_connection_unlocked(self, user_id: int) -> None:
        """End connection without acquiring lock (internal use only)."""
        uid_str = str(user_id)
        if uid_str in self._data['connections']:
            del self._data['connections'][uid_str]
        for uid, conn in list(self._data['connections'].items()):
            if conn['target_id'] == uid_str:
                del self._data['connections'][uid]

    async def end_connection(self, user_id: int) -> None:
        """End connection for a user."""
        async with self._lock:
            await self._end_connection_unlocked(user_id)
            await self._save()

    def get_connection(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get connection data for user."""
        return self._data['connections'].get(str(user_id))

    async def increment_message_count(self, user_id: int) -> None:
        """Increment message count for both connected users."""
        async with self._lock:
            conn = self._data['connections'].get(str(user_id))
            if conn:
                conn['message_count'] = conn.get('message_count', 0) + 1
                target_conn = self._data['connections'].get(conn['target_id'])
                if target_conn:
                    target_conn['message_count'] = target_conn.get('message_count', 0) + 1
                await self._save()

    def get_message_count(self, user_id: int) -> int:
        """Get message count for connection."""
        conn = self.get_connection(user_id)
        return conn.get('message_count', 0) if conn else 0

    # ----- Rate Limiting -----

    async def add_message_timestamp(self, user_id: int, target_id: int) -> None:
        """Add message timestamp for rate limiting."""
        async with self._lock:
            user = self._data['users'].get(str(user_id))
            if not user:
                return
            timestamps = user['message_timestamps'].setdefault(str(target_id), [])
            now = datetime.now(timezone.utc)
            timestamps.append(now.isoformat())
            # Clean up timestamps older than 1 minute
            user['message_timestamps'][str(target_id)] = [
                ts for ts in timestamps
                if (now - datetime.fromisoformat(ts)).total_seconds() <= 60
            ]
            await self._save()

    def get_message_count_in_window(self, user_id: int, target_id: int) -> int:
        """Get message count in the last minute."""
        user = self.get_user(user_id)
        if not user or str(target_id) not in user.get('message_timestamps', {}):
            return 0
        return len(user['message_timestamps'][str(target_id)])

    # ----- Inactivity Check -----

    def get_inactive_users(self, timeout_minutes: int = 5) -> List[Dict[str, Any]]:
        """Get users who have been inactive beyond timeout."""
        inactive_users = []
        current_time = datetime.now(timezone.utc)
        processed_users = set()

        for uid, user in self._data['users'].items():
            if user.get('banned', False) or self.is_banned(int(uid)):
                continue
            last_activity_str = user.get('last_activity')
            if not last_activity_str:
                continue
            try:
                last_activity = datetime.fromisoformat(last_activity_str)
                if (current_time - last_activity).total_seconds() / 60 > timeout_minutes:
                    if str(uid) in self._data['connections'] and uid not in processed_users:
                        inactive_users.append({"user_id": int(uid), "user_data": user})
                        processed_users.add(uid)
            except ValueError:
                logger.error(f"Invalid last_activity format for user {uid}")

        return inactive_users


# Global store instance - initialized in main
store: Optional[JSONStore] = None


def init_store(data_file: str) -> JSONStore:
    """Initialize the global store."""
    global store
    store = JSONStore(data_file)
    return store


def get_store() -> JSONStore:
    """Get the global store instance."""
    if store is None:
        raise RuntimeError("Store not initialized. Call init_store() first.")
    return store
