"""Thread-safe JSON data store with asyncio locking."""

import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


def generate_special_code(user_id: int) -> str:
    """Generate a UID special code tied to user_id. Format: 8 chars alphanumeric."""
    import string
    # Create deterministic hash from user_id
    hash_input = f"anonic_{user_id}_uid"
    hash_bytes = hashlib.sha256(hash_input.encode()).digest()
    # Convert to alphanumeric (letters + numbers)
    chars = string.ascii_lowercase + string.digits
    result = ""
    for i in range(8):
        result += chars[hash_bytes[i] % len(chars)]
    return result


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
    DEFAULT_ALLOWED = [
        "text", "emoji", "emojionly", "emojicustom", "cyrillic",
        "photo", "video", "gif", "voice", "videonote", "audio", "document",
        "sticker", "stickeranimated", "stickerpremium",
        "spoiler", "url",
    ]

    def __init__(self, path: str):
        self.path = path
        self._lock = asyncio.Lock()
        self._data: Dict[str, Any] = {
            "users": {},
            "blocks": {},
            "pending_targets": {},  # One-time: {sender_id: {target_id}} - cleared after first msg
            "messages": {}          # Reply routing: {bot_msg_id: {sender_id, receiver_id}}
        }

        if not os.path.exists(path):
            self._save_sync()
        else:
            self._load_sync()
            # Migrate old data structures
            if "connections" in self._data:
                del self._data["connections"]
            if "sessions" in self._data:
                del self._data["sessions"]
            if "pending_targets" not in self._data:
                self._data["pending_targets"] = {}
            if "messages" not in self._data:
                self._data["messages"] = {}
            if "temp_links" not in self._data:
                self._data["temp_links"] = {}
            self._save_sync()

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

    async def add_user(
        self,
        telegram_id: int,
        token: str,
        nickname: str,
        language_code: str = "en",
        username: str = None,
        first_name: str = None,
        last_name: str = None
    ) -> None:
        """Add a new user to the store."""
        async with self._lock:
            special_code = generate_special_code(telegram_id)
            self._data['users'][str(telegram_id)] = {
                "token": token,
                "nickname": nickname,
                "special_code": special_code,
                "registered_at": datetime.now(timezone.utc).isoformat(),
                "allowed_types": self.DEFAULT_ALLOWED.copy(),
                "last_activity": datetime.now(timezone.utc).isoformat(),
                "lang": language_code or "en",
                "language_code": language_code or "en",
                "username": username,
                "first_name": first_name,
                "last_name": last_name,
                "banned": False,
                "ban_expires_at": None,
                "message_timestamps": {},
                "last_revoke": None,
                "protect_content": False,
                "messages_sent": 0,
                "messages_received": 0
            }
            await self._save()

    async def revoke_user(self, telegram_id: int, new_token: str, new_nickname: str) -> Tuple[bool, str]:
        """Revoke user data and generate new token/nickname. Returns (success, error_reason).

        Keeps history of old tokens/nicknames and counts total revokes.
        User data is never deleted.
        """
        async with self._lock:
            user = self._data['users'].get(str(telegram_id))
            if not user:
                return False, "not_found"

            # Check weekly limit
            last_revoke = user.get('last_revoke')
            if last_revoke:
                try:
                    last_revoke_dt = datetime.fromisoformat(last_revoke)
                    days_since = (datetime.now(timezone.utc) - last_revoke_dt).days
                    if days_since < 7:
                        return False, f"wait_{7 - days_since}"
                except ValueError:
                    pass

            # Initialize history if not exists
            if 'revoke_history' not in user:
                user['revoke_history'] = []
            if 'revoke_count' not in user:
                user['revoke_count'] = 0

            # Store old data in history
            user['revoke_history'].append({
                'old_token': user['token'],
                'old_nickname': user['nickname'],
                'registered_at': user.get('registered_at'),
                'revoked_at': datetime.now(timezone.utc).isoformat()
            })

            # Update user data with new token/nickname
            user['token'] = new_token
            user['nickname'] = new_nickname
            user['last_revoke'] = datetime.now(timezone.utc).isoformat()
            user['revoke_count'] = user['revoke_count'] + 1

            await self._save()
            return True, ""

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

    def find_user_by_special_code(self, special_code: str) -> Optional[int]:
        """Find user ID by special code."""
        for user_id, u in self._data['users'].items():
            if u.get('special_code') == special_code:
                return int(user_id)
        return None

    def get_user_special_code(self, telegram_id: int) -> str:
        """Get or generate special code for user. Auto-generates if missing."""
        user = self._data['users'].get(str(telegram_id))
        if not user:
            return ""
        if 'special_code' not in user or not user['special_code']:
            # Generate and store for existing users without special_code
            user['special_code'] = generate_special_code(telegram_id)
            self._save_sync()  # Sync save for read operation
        return user['special_code']

    # ----- Security Settings -----

    async def set_protect_content(self, telegram_id: int, enabled: bool) -> bool:
        """Set protect_content setting for user."""
        async with self._lock:
            user = self._data['users'].get(str(telegram_id))
            if user:
                user['protect_content'] = enabled
                await self._save()
                return True
            return False

    def get_protect_content(self, telegram_id: int) -> bool:
        """Get user's protect_content setting."""
        user = self.get_user(telegram_id)
        return user.get('protect_content', False) if user else False

    # ----- Message Stats -----

    async def increment_messages_sent(self, telegram_id: int) -> None:
        """Increment messages sent counter."""
        async with self._lock:
            user = self._data['users'].get(str(telegram_id))
            if user:
                user['messages_sent'] = user.get('messages_sent', 0) + 1
                user['last_activity'] = datetime.now(timezone.utc).isoformat()
                await self._save()

    async def increment_messages_received(self, telegram_id: int) -> None:
        """Increment messages received counter."""
        async with self._lock:
            user = self._data['users'].get(str(telegram_id))
            if user:
                user['messages_received'] = user.get('messages_received', 0) + 1
                user['last_activity'] = datetime.now(timezone.utc).isoformat()
                await self._save()

    def get_user_stats(self, telegram_id: int) -> Dict[str, Any]:
        """Get user statistics."""
        user = self.get_user(telegram_id)
        if not user:
            return {}
        return {
            'messages_sent': user.get('messages_sent', 0),
            'messages_received': user.get('messages_received', 0),
            'registered_at': user.get('registered_at'),
            'last_activity': user.get('last_activity'),
            'blocked_count': self.get_blocked_count(str(telegram_id)),
            'revoke_count': user.get('revoke_count', 0),
            'protect_content': user.get('protect_content', False)
        }

    # ----- Admin Stats -----

    def get_admin_stats(self) -> Dict[str, Any]:
        """Get admin statistics for all users."""
        now = datetime.now(timezone.utc)
        total_users = len(self._data['users'])
        active_24h = 0
        active_7d = 0
        total_messages = 0
        total_banned = 0

        for user in self._data['users'].values():
            total_messages += user.get('messages_sent', 0) + user.get('messages_received', 0)
            if user.get('banned', False):
                total_banned += 1
            try:
                last_activity = datetime.fromisoformat(user.get('last_activity', ''))
                hours_since = (now - last_activity).total_seconds() / 3600
                if hours_since <= 24:
                    active_24h += 1
                if hours_since <= 168:  # 7 days
                    active_7d += 1
            except (ValueError, TypeError):
                pass

        return {
            'total_users': total_users,
            'active_24h': active_24h,
            'active_7d': active_7d,
            'total_messages': total_messages,
            'total_banned': total_banned,
            'temp_links_count': len(self._data.get('temp_links', {}))
        }

    # ----- Ban Management -----

    async def ban_user(self, telegram_id: int, duration: Optional[timedelta] = None) -> bool:
        """Ban a user, optionally for a duration."""
        async with self._lock:
            user = self._data['users'].get(str(telegram_id))
            if user and not user.get('banned', False):
                user['banned'] = True
                user['ban_expires_at'] = (datetime.now(timezone.utc) + duration).isoformat() if duration else None
                # Clear pending target if exists
                if str(telegram_id) in self._data.get('pending_targets', {}):
                    del self._data['pending_targets'][str(telegram_id)]
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

    async def block(self, recipient_id: str, blocked_user_id: int, nickname: str) -> None:
        """Block a user by user_id. Nickname and special_code stored for display."""
        async with self._lock:
            blocks = self._data['blocks'].setdefault(recipient_id, [])
            # Check if already blocked by user_id
            if not any(block.get("user_id") == str(blocked_user_id) for block in blocks):
                special_code = self.get_user_special_code(blocked_user_id)
                blocks.append({
                    "user_id": str(blocked_user_id),
                    "nickname": nickname,
                    "special_code": special_code
                })
                await self._save()

    async def unblock(self, recipient_id: str, identifier: str) -> bool:
        """Unblock a user by special_code or nickname."""
        async with self._lock:
            if recipient_id not in self._data['blocks']:
                return False
            for block in self._data['blocks'][recipient_id][:]:
                # Match by special_code (exact) or nickname (case insensitive)
                if (block.get("special_code") == identifier or
                    identifier.lower() in block["nickname"].lower()):
                    self._data['blocks'][recipient_id].remove(block)
                    await self._save()
                    return True
            return False

    async def unblock_all(self, recipient_id: str) -> int:
        """Unblock all users. Returns count of unblocked users."""
        async with self._lock:
            if recipient_id not in self._data['blocks']:
                return 0
            count = len(self._data['blocks'][recipient_id])
            self._data['blocks'][recipient_id] = []
            await self._save()
            return count

    def is_blocked_by_user_id(self, recipient_id: str, blocked_user_id: int) -> bool:
        """Check if a user_id is blocked by recipient."""
        if recipient_id not in self._data['blocks']:
            return False
        return any(block.get("user_id") == str(blocked_user_id) for block in self._data['blocks'][recipient_id])

    def is_blocked(self, recipient_id: str, nickname: str) -> bool:
        """Check if a nickname is blocked by recipient (legacy, for display)."""
        if recipient_id not in self._data['blocks']:
            return False
        return any(block["nickname"] == nickname for block in self._data['blocks'][recipient_id])

    def get_blocked_users(self, recipient_id: str) -> List[str]:
        """Get list of blocked users for display: {nickname} {special_code}."""
        if recipient_id not in self._data['blocks']:
            return []
        return [
            f"<b>{block['nickname']}</b> <code>{block.get('special_code', 'N/A')}</code>"
            for block in self._data['blocks'][recipient_id]
        ]

    def get_blocked_count(self, recipient_id: str) -> int:
        """Get count of blocked users."""
        if recipient_id not in self._data['blocks']:
            return 0
        return len(self._data['blocks'][recipient_id])

    def is_user_blocked(self, recipient_id: str, identifier: str) -> bool:
        """Check if identifier (special_code or nickname) is blocked."""
        if recipient_id not in self._data['blocks']:
            return False
        return any(
            block.get("special_code") == identifier or identifier.lower() in block["nickname"].lower()
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

    async def reset_allowed_types(self, user_id: str) -> bool:
        """Reset allowed types to defaults."""
        async with self._lock:
            user = self._data['users'].get(user_id)
            if not user:
                return False
            user['allowed_types'] = self.DEFAULT_ALLOWED.copy()
            await self._save()
            return True

    def get_allowed_types(self, user_id: str) -> List[str]:
        """Get allowed message types for user."""
        user = self._data['users'].get(user_id)
        return user.get('allowed_types', ["text"]) if user else ["text"]

    # ----- Pending Target (One-Time Deep Link Routing) -----

    async def set_pending_target(self, sender_id: int, target_id: int) -> None:
        """Set one-time pending target from deep link. Cleared after first message."""
        async with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            if 'pending_targets' not in self._data:
                self._data['pending_targets'] = {}
            self._data['pending_targets'][str(sender_id)] = {
                "target_id": str(target_id),
                "created_at": now
            }
            # Update activity
            user = self._data['users'].get(str(sender_id))
            if user:
                user['last_activity'] = now
            await self._save()

    def get_pending_target(self, sender_id: int) -> Optional[int]:
        """Get sender's pending target (one-time, from deep link)."""
        pending = self._data.get('pending_targets', {}).get(str(sender_id))
        if pending:
            return int(pending['target_id'])
        return None

    async def clear_pending_target(self, sender_id: int) -> None:
        """Clear sender's pending target after first message."""
        async with self._lock:
            if str(sender_id) in self._data.get('pending_targets', {}):
                del self._data['pending_targets'][str(sender_id)]
                await self._save()

    # ----- Message Tracking (for Reply Routing) -----

    async def store_message(self, bot_msg_id: int, sender_id: int, receiver_id: int) -> None:
        """Store message for reply routing.

        When bot forwards a message to receiver, store the mapping so receiver
        can reply back to the original sender.
        """
        async with self._lock:
            self._data['messages'][str(bot_msg_id)] = {
                "sender_id": str(sender_id),
                "receiver_id": str(receiver_id),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            await self._save()

    def get_message_sender(self, bot_msg_id: int) -> Optional[int]:
        """Get original sender ID from bot's message ID (for reply routing)."""
        msg_data = self._data.get('messages', {}).get(str(bot_msg_id))
        if msg_data:
            return int(msg_data['sender_id'])
        return None

    def get_message_data(self, bot_msg_id: int) -> Optional[Dict[str, Any]]:
        """Get full message data for a bot message ID."""
        return self._data.get('messages', {}).get(str(bot_msg_id))

    async def cleanup_old_messages(self, max_age_hours: int = 24) -> int:
        """Clean up messages older than max_age_hours. Returns count deleted."""
        async with self._lock:
            now = datetime.now(timezone.utc)
            to_delete = []
            for msg_id, msg_data in self._data.get('messages', {}).items():
                try:
                    timestamp = datetime.fromisoformat(msg_data['timestamp'])
                    if (now - timestamp).total_seconds() / 3600 > max_age_hours:
                        to_delete.append(msg_id)
                except (ValueError, KeyError):
                    to_delete.append(msg_id)

            for msg_id in to_delete:
                del self._data['messages'][msg_id]

            if to_delete:
                await self._save()
            return len(to_delete)

    # ----- Legacy Support (backward compatibility) -----

    def get_session(self, sender_id: int) -> Optional[Dict[str, Any]]:
        """Legacy: Get pending target as session-like dict."""
        target_id = self.get_pending_target(sender_id)
        if target_id:
            return {"target_id": str(target_id)}
        return None

    async def clear_session(self, sender_id: int) -> None:
        """Legacy: Clear pending target."""
        await self.clear_pending_target(sender_id)

    def get_connection(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Legacy: Alias for get_session."""
        return self.get_session(user_id)

    async def end_connection(self, user_id: int) -> None:
        """Legacy: Alias for clear_session."""
        await self.clear_session(user_id)

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

    def get_expired_pending_targets(self, timeout_minutes: int = 5) -> List[int]:
        """Get pending targets that have expired (not used within timeout)."""
        expired = []
        current_time = datetime.now(timezone.utc)

        for uid, pending in self._data.get('pending_targets', {}).items():
            try:
                created_at = datetime.fromisoformat(pending.get('created_at', ''))
                if (current_time - created_at).total_seconds() / 60 > timeout_minutes:
                    expired.append(int(uid))
            except ValueError:
                expired.append(int(uid))

        return expired

    async def cleanup_expired_pending_targets(self, timeout_minutes: int = 5) -> int:
        """Clean up expired pending targets. Returns count deleted."""
        expired = self.get_expired_pending_targets(timeout_minutes)
        async with self._lock:
            for uid in expired:
                if str(uid) in self._data.get('pending_targets', {}):
                    del self._data['pending_targets'][str(uid)]
            if expired:
                await self._save()
        return len(expired)

    # ----- Temporary Links -----

    def _generate_temp_token(self) -> str:
        """Generate unique temp link token."""
        import secrets
        return secrets.token_urlsafe(12)

    async def create_temp_link(
        self,
        user_id: int,
        expires_days: Optional[int] = None,
        max_uses: Optional[int] = None
    ) -> str:
        """Create a temporary link with optional expiration and usage limit."""
        async with self._lock:
            if 'temp_links' not in self._data:
                self._data['temp_links'] = {}

            token = self._generate_temp_token()
            now = datetime.now(timezone.utc)

            self._data['temp_links'][token] = {
                'user_id': str(user_id),
                'created_at': now.isoformat(),
                'expires_at': (now + timedelta(days=expires_days)).isoformat() if expires_days else None,
                'max_uses': max_uses,
                'current_uses': 0,
                'active': True
            }
            await self._save()
            return token

    def get_temp_link(self, token: str) -> Optional[Dict[str, Any]]:
        """Get temp link data by token."""
        return self._data.get('temp_links', {}).get(token)

    def get_user_by_temp_link(self, token: str) -> Tuple[Optional[int], Optional[Dict[str, Any]]]:
        """Find user by temp link token. Returns (user_id, user_data) or (None, None)."""
        link = self.get_temp_link(token)
        if not link or not link.get('active', True):
            return None, None

        # Check expiration
        if link.get('expires_at'):
            try:
                expires_at = datetime.fromisoformat(link['expires_at'])
                if datetime.now(timezone.utc) > expires_at:
                    return None, None
            except ValueError:
                pass

        # Check usage limit
        if link.get('max_uses') is not None:
            if link.get('current_uses', 0) >= link['max_uses']:
                return None, None

        user_id = int(link['user_id'])
        user = self.get_user(user_id)
        return user_id, user

    async def use_temp_link(self, token: str) -> bool:
        """Increment usage counter for temp link."""
        async with self._lock:
            link = self._data.get('temp_links', {}).get(token)
            if link and link.get('active', True):
                link['current_uses'] = link.get('current_uses', 0) + 1
                await self._save()
                return True
            return False

    async def revoke_temp_link(self, token: str, user_id: int) -> bool:
        """Revoke a temp link (must be owned by user)."""
        async with self._lock:
            link = self._data.get('temp_links', {}).get(token)
            if link and link.get('user_id') == str(user_id):
                link['active'] = False
                await self._save()
                return True
            return False

    async def delete_temp_link(self, token: str, user_id: int) -> bool:
        """Delete a temp link completely (must be owned by user)."""
        async with self._lock:
            link = self._data.get('temp_links', {}).get(token)
            if link and link.get('user_id') == str(user_id):
                del self._data['temp_links'][token]
                await self._save()
                return True
            return False

    async def delete_all_temp_links(self, user_id: int) -> int:
        """Delete all temp links for a user. Returns count deleted."""
        async with self._lock:
            to_delete = [
                token for token, link in self._data.get('temp_links', {}).items()
                if link.get('user_id') == str(user_id)
            ]
            for token in to_delete:
                del self._data['temp_links'][token]
            if to_delete:
                await self._save()
            return len(to_delete)

    def get_user_temp_links(self, user_id: int) -> List[Dict[str, Any]]:
        """Get all temp links for a user."""
        links = []
        for token, link in self._data.get('temp_links', {}).items():
            if link.get('user_id') == str(user_id):
                link_info = {
                    'token': token,
                    **link
                }
                links.append(link_info)
        return links

    def get_active_temp_links(self, user_id: int) -> List[Dict[str, Any]]:
        """Get active (non-expired, not max used) temp links for a user."""
        links = []
        now = datetime.now(timezone.utc)

        for token, link in self._data.get('temp_links', {}).items():
            if link.get('user_id') != str(user_id):
                continue
            if not link.get('active', True):
                continue

            # Check expiration
            if link.get('expires_at'):
                try:
                    expires_at = datetime.fromisoformat(link['expires_at'])
                    if now > expires_at:
                        continue
                except ValueError:
                    pass

            # Check usage limit
            if link.get('max_uses') is not None:
                if link.get('current_uses', 0) >= link['max_uses']:
                    continue

            link_info = {'token': token, **link}
            links.append(link_info)

        return links

    async def cleanup_expired_temp_links(self) -> int:
        """Clean up expired temp links. Returns count deleted."""
        async with self._lock:
            now = datetime.now(timezone.utc)
            to_delete = []

            for token, link in self._data.get('temp_links', {}).items():
                # Delete if inactive
                if not link.get('active', True):
                    to_delete.append(token)
                    continue
                # Delete if expired
                if link.get('expires_at'):
                    try:
                        expires_at = datetime.fromisoformat(link['expires_at'])
                        if now > expires_at:
                            to_delete.append(token)
                    except ValueError:
                        to_delete.append(token)

            for token in to_delete:
                del self._data['temp_links'][token]

            if to_delete:
                await self._save()
            return len(to_delete)


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
