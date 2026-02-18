"""SQLite-backed data store with async writes and sync reads."""

import asyncio
import hashlib
import json
import logging
import secrets
import sqlite3
import string
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite

from ..utils import generate_profile_token
from .schema import INDEXES_SQL, SCHEMA_SQL

logger = logging.getLogger(__name__)


def generate_special_code(user_id: int) -> str:
    """Generate a UID special code tied to user_id. Format: 8 chars alphanumeric."""
    chars = string.ascii_lowercase + string.digits
    hash_input = f"anonic_{user_id}_uid"
    hash_bytes = hashlib.sha256(hash_input.encode()).digest()
    return "".join(chars[hash_bytes[i] % len(chars)] for i in range(8))


class SQLiteStore:
    """SQLite-backed store with sync reads (sqlite3) and async writes (aiosqlite)."""

    VALID_TYPES = [
        "all",
        "text", "url", "email", "phone", "cashtag", "hashtag", "spoiler",
        "emoji", "emojionly", "emojicustom", "cyrillic", "zalgo",
        "photo", "video", "gif", "voice", "videonote", "audio", "document",
        "sticker", "stickeranimated", "stickerpremium",
        "location", "poll", "inline", "button", "game", "emojigame",
        "forward", "forwardbot", "forwardchannel", "forwardstory", "forwarduser",
        "externalreply",
    ]
    BLOCKED_TYPES = ["contact", "venue", "successful_payment"]
    DEFAULT_ALLOWED = [
        "text", "emoji", "emojionly", "emojicustom", "cyrillic",
        "photo", "video", "gif", "voice", "videonote", "audio", "document",
        "sticker", "stickeranimated", "stickerpremium",
        "spoiler", "url", "hashtag",
    ]

    def __init__(self, path: str):
        self.path = path
        self._read_conn: Optional[sqlite3.Connection] = None
        self._write_conn: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        """Create tables, indexes, and open connections."""
        # Open async connection for writes, create schema
        self._write_conn = await aiosqlite.connect(self.path)
        await self._write_conn.execute("PRAGMA journal_mode=WAL;")
        await self._write_conn.execute("PRAGMA foreign_keys=ON;")
        await self._write_conn.executescript(SCHEMA_SQL)
        for idx_sql in INDEXES_SQL:
            await self._write_conn.execute(idx_sql)
        await self._write_conn.commit()

        # Migrations
        for col in (
            "avatar TEXT",
            "frame TEXT",
            "profile_token TEXT",
            "profile_public INTEGER DEFAULT 0",
            "profile_show_last_seen INTEGER DEFAULT 0",
            "profile_show_level INTEGER DEFAULT 1",
            "profile_show_active_days INTEGER DEFAULT 1",
            "profile_show_registered INTEGER DEFAULT 1",
        ):
            try:
                await self._write_conn.execute(f"ALTER TABLE users ADD COLUMN {col}")
                await self._write_conn.commit()
            except Exception:
                pass  # column already exists

        # Backfill profile_token for existing users
        try:
            await self._write_conn.execute(
                "UPDATE users SET profile_token = hex(randomblob(6)) WHERE profile_token IS NULL"
            )
            await self._write_conn.commit()
        except Exception:
            pass

        # Open sync connection for reads (read-only via WAL)
        self._read_conn = sqlite3.connect(self.path)
        self._read_conn.row_factory = sqlite3.Row
        self._read_conn.execute("PRAGMA journal_mode=WAL;")
        self._read_conn.execute("PRAGMA query_only=ON;")

    async def close(self) -> None:
        """Close all database connections."""
        if self._write_conn:
            await self._write_conn.close()
        if self._read_conn:
            self._read_conn.close()

    # ---- helpers ----

    def _row_to_user_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        """Convert a users table row to the dict format handlers expect."""
        d = dict(row)
        d["allowed_types"] = json.loads(d["allowed_types"]) if d["allowed_types"] else ["text"]
        d["banned"] = bool(d["banned"])
        d["is_premium"] = bool(d["is_premium"])
        d["protect_content"] = bool(d["protect_content"])
        d["profile_public"] = bool(d.get("profile_public"))
        d["profile_show_last_seen"] = bool(d.get("profile_show_last_seen"))
        d["profile_show_level"] = bool(d.get("profile_show_level", 1))
        d["profile_show_active_days"] = bool(d.get("profile_show_active_days", 1))
        d["profile_show_registered"] = bool(d.get("profile_show_registered", 1))
        d["message_timestamps"] = {}  # rate-limit timestamps live in separate table
        return d

    def _fetchone_user(self, telegram_id: int) -> Optional[sqlite3.Row]:
        cur = self._read_conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        )
        return cur.fetchone()

    # ---- User Management ----

    async def add_user(
        self,
        telegram_id: int,
        token: str,
        nickname: str,
        language_code: str = "en",
        username: str = None,
        first_name: str = None,
        last_name: str = None,
        is_premium: bool = False,
        frame: str = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        special_code = generate_special_code(telegram_id)
        allowed = json.dumps(self.DEFAULT_ALLOWED)
        profile_token = generate_profile_token()
        await self._write_conn.execute(
            """INSERT OR IGNORE INTO users
               (telegram_id, token, nickname, special_code, registered_at,
                last_activity, lang, username, first_name, last_name,
                is_premium, allowed_types, frame, profile_token)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (telegram_id, token, nickname, special_code, now, now,
             language_code or "en", username, first_name, last_name,
             int(is_premium), allowed, frame, profile_token),
        )
        await self._write_conn.commit()

    async def revoke_user(
        self, telegram_id: int, new_token: str, new_nickname: str,
        new_frame: str = None,
    ) -> Tuple[bool, str]:
        row = self._fetchone_user(telegram_id)
        if not row:
            return False, "not_found"

        user = dict(row)
        last_revoke = user.get("last_revoke")
        if last_revoke:
            try:
                last_revoke_dt = datetime.fromisoformat(last_revoke)
                days_since = (datetime.now(timezone.utc) - last_revoke_dt).days
                if days_since < 7:
                    return False, f"wait_{7 - days_since}"
            except ValueError:
                pass

        now = datetime.now(timezone.utc).isoformat()
        new_profile_token = generate_profile_token()
        await self._write_conn.execute(
            """INSERT INTO revoke_history
               (user_id, old_token, old_nickname, registered_at, revoked_at)
               VALUES (?, ?, ?, ?, ?)""",
            (telegram_id, user["token"], user["nickname"],
             user.get("registered_at"), now),
        )
        await self._write_conn.execute(
            """UPDATE users SET token = ?, nickname = ?, last_revoke = ?,
               revoke_count = revoke_count + 1, frame = ?,
               profile_token = ?
               WHERE telegram_id = ?""",
            (new_token, new_nickname, now, new_frame, new_profile_token, telegram_id),
        )
        await self._write_conn.execute(
            "DELETE FROM temp_links WHERE user_id = ?", (telegram_id,)
        )
        await self._write_conn.commit()

        # Delete old avatar so it regenerates with the new nickname
        try:
            from ..webapp import delete_avatar_file
            delete_avatar_file(telegram_id)
        except Exception:
            pass

        return True, ""

    def get_user(self, telegram_id: int) -> Optional[Dict[str, Any]]:
        row = self._fetchone_user(telegram_id)
        return self._row_to_user_dict(row) if row else None

    def get_user_language(self, telegram_id: int) -> str:
        cur = self._read_conn.execute(
            "SELECT lang FROM users WHERE telegram_id = ?", (telegram_id,)
        )
        row = cur.fetchone()
        return row["lang"] if row and row["lang"] else "en"

    async def set_user_language(
        self, telegram_id: int, lang: str, available_langs: List[str]
    ) -> bool:
        if lang not in available_langs:
            return False
        cur = await self._write_conn.execute(
            "UPDATE users SET lang = ? WHERE telegram_id = ?",
            (lang, telegram_id),
        )
        await self._write_conn.commit()
        return cur.rowcount > 0

    async def update_last_activity(
        self,
        telegram_id: int,
        username: str = None,
        first_name: str = None,
        last_name: str = None,
        is_premium: bool = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        sets = ["last_activity = ?"]
        params: list = [now]
        if username is not None:
            sets.append("username = ?"); params.append(username)
        if first_name is not None:
            sets.append("first_name = ?"); params.append(first_name)
        if last_name is not None:
            sets.append("last_name = ?"); params.append(last_name)
        if is_premium is not None:
            sets.append("is_premium = ?"); params.append(int(is_premium))
        params.append(telegram_id)
        await self._write_conn.execute(
            f"UPDATE users SET {', '.join(sets)} WHERE telegram_id = ?", params
        )
        await self._write_conn.commit()

    def get_by_token(self, token: str) -> Tuple[Optional[int], Optional[Dict[str, Any]]]:
        cur = self._read_conn.execute(
            "SELECT * FROM users WHERE token = ?", (token,)
        )
        row = cur.fetchone()
        if row:
            return row["telegram_id"], self._row_to_user_dict(row)
        return None, None

    def find_user_by_nickname(self, nickname: str) -> Optional[int]:
        cur = self._read_conn.execute(
            "SELECT telegram_id FROM users WHERE TRIM(nickname) = ?",
            (nickname.strip(),),
        )
        row = cur.fetchone()
        return row["telegram_id"] if row else None

    def find_user_by_special_code(self, special_code: str) -> Optional[int]:
        cur = self._read_conn.execute(
            "SELECT telegram_id FROM users WHERE special_code = ?",
            (special_code,),
        )
        row = cur.fetchone()
        return row["telegram_id"] if row else None

    def get_user_special_code(self, telegram_id: int) -> str:
        cur = self._read_conn.execute(
            "SELECT special_code FROM users WHERE telegram_id = ?",
            (telegram_id,),
        )
        row = cur.fetchone()
        if not row:
            return ""
        code = row["special_code"]
        if not code:
            code = generate_special_code(telegram_id)
            # Fire-and-forget async write
            asyncio.get_event_loop().create_task(self._set_special_code(telegram_id, code))
        return code

    async def _set_special_code(self, telegram_id: int, code: str) -> None:
        await self._write_conn.execute(
            "UPDATE users SET special_code = ? WHERE telegram_id = ?",
            (code, telegram_id),
        )
        await self._write_conn.commit()

    # ---- Avatar ----

    async def set_avatar(self, user_id: int, path: str) -> None:
        await self._write_conn.execute(
            "UPDATE users SET avatar = ? WHERE telegram_id = ?",
            (path, user_id),
        )
        await self._write_conn.commit()

    async def delete_avatar(self, user_id: int) -> None:
        await self._write_conn.execute(
            "UPDATE users SET avatar = NULL WHERE telegram_id = ?",
            (user_id,),
        )
        await self._write_conn.commit()

    # ---- Security Settings ----

    async def set_protect_content(self, telegram_id: int, enabled: bool) -> bool:
        cur = await self._write_conn.execute(
            "UPDATE users SET protect_content = ? WHERE telegram_id = ?",
            (int(enabled), telegram_id),
        )
        await self._write_conn.commit()
        return cur.rowcount > 0

    def get_protect_content(self, telegram_id: int) -> bool:
        cur = self._read_conn.execute(
            "SELECT protect_content FROM users WHERE telegram_id = ?",
            (telegram_id,),
        )
        row = cur.fetchone()
        return bool(row["protect_content"]) if row else False

    # ---- Message Stats ----

    async def increment_messages_sent(self, telegram_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._write_conn.execute(
            "UPDATE users SET messages_sent = messages_sent + 1, last_activity = ? WHERE telegram_id = ?",
            (now, telegram_id),
        )
        await self._write_conn.commit()

    async def increment_messages_received(self, telegram_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._write_conn.execute(
            "UPDATE users SET messages_received = messages_received + 1, last_activity = ? WHERE telegram_id = ?",
            (now, telegram_id),
        )
        await self._write_conn.commit()

    def get_user_stats(self, telegram_id: int) -> Dict[str, Any]:
        user = self.get_user(telegram_id)
        if not user:
            return {}
        return {
            "messages_sent": user.get("messages_sent", 0),
            "messages_received": user.get("messages_received", 0),
            "registered_at": user.get("registered_at"),
            "last_activity": user.get("last_activity"),
            "blocked_count": self.get_blocked_count(str(telegram_id)),
            "revoke_count": user.get("revoke_count", 0),
            "protect_content": user.get("protect_content", False),
        }

    # ---- Admin Stats ----

    def get_admin_stats(self) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        ts_24h = (now - timedelta(hours=24)).isoformat()
        ts_7d = (now - timedelta(days=7)).isoformat()

        total = self._read_conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        active_24h = self._read_conn.execute(
            "SELECT COUNT(*) FROM users WHERE last_activity >= ?", (ts_24h,)
        ).fetchone()[0]
        active_7d = self._read_conn.execute(
            "SELECT COUNT(*) FROM users WHERE last_activity >= ?", (ts_7d,)
        ).fetchone()[0]
        msg_row = self._read_conn.execute(
            "SELECT COALESCE(SUM(messages_sent + messages_received), 0) FROM users"
        ).fetchone()
        total_messages = msg_row[0]
        total_banned = self._read_conn.execute(
            "SELECT COUNT(*) FROM users WHERE banned = 1"
        ).fetchone()[0]
        temp_links_count = self._read_conn.execute(
            "SELECT COUNT(*) FROM temp_links WHERE active = 1"
        ).fetchone()[0]

        return {
            "total_users": total,
            "active_24h": active_24h,
            "active_7d": active_7d,
            "total_messages": total_messages,
            "total_banned": total_banned,
            "temp_links_count": temp_links_count,
        }

    # ---- Ban Management ----

    async def ban_user(
        self, telegram_id: int, duration: Optional[timedelta] = None
    ) -> bool:
        row = self._fetchone_user(telegram_id)
        if not row or row["banned"]:
            return False
        expires = (
            (datetime.now(timezone.utc) + duration).isoformat() if duration else None
        )
        await self._write_conn.execute(
            "UPDATE users SET banned = 1, ban_expires_at = ? WHERE telegram_id = ?",
            (expires, telegram_id),
        )
        await self._write_conn.execute(
            "DELETE FROM pending_targets WHERE sender_id = ?", (telegram_id,)
        )
        await self._write_conn.commit()
        return True

    async def unban_user(self, telegram_id: int) -> bool:
        cur = await self._write_conn.execute(
            "UPDATE users SET banned = 0, ban_expires_at = NULL WHERE telegram_id = ? AND banned = 1",
            (telegram_id,),
        )
        await self._write_conn.commit()
        return cur.rowcount > 0

    def is_banned(self, telegram_id: int) -> bool:
        cur = self._read_conn.execute(
            "SELECT banned, ban_expires_at FROM users WHERE telegram_id = ?",
            (telegram_id,),
        )
        row = cur.fetchone()
        if not row or not row["banned"]:
            return False
        ban_expires_at = row["ban_expires_at"]
        if ban_expires_at:
            try:
                expiry = datetime.fromisoformat(ban_expires_at)
                if datetime.now(timezone.utc) > expiry:
                    asyncio.get_event_loop().create_task(self.unban_user(telegram_id))
                    return False
            except ValueError:
                logger.error(
                    "Invalid ban_expires_at for user %s: %s",
                    telegram_id, ban_expires_at,
                )
        return True

    # ---- Block Management ----

    async def block(
        self, recipient_id: str, blocked_user_id: int, nickname: str
    ) -> None:
        special_code = self.get_user_special_code(blocked_user_id)
        await self._write_conn.execute(
            """INSERT OR IGNORE INTO blocks
               (recipient_id, blocked_user_id, nickname, special_code)
               VALUES (?, ?, ?, ?)""",
            (int(recipient_id), blocked_user_id, nickname, special_code),
        )
        await self._write_conn.commit()

    async def unblock(self, recipient_id: str, identifier: str) -> bool:
        # Try exact special_code match first
        cur = await self._write_conn.execute(
            "DELETE FROM blocks WHERE recipient_id = ? AND special_code = ?",
            (int(recipient_id), identifier),
        )
        if cur.rowcount > 0:
            await self._write_conn.commit()
            return True
        # Fallback: case-insensitive nickname substring match
        rows = self._read_conn.execute(
            "SELECT id, nickname FROM blocks WHERE recipient_id = ?",
            (int(recipient_id),),
        ).fetchall()
        for row in rows:
            if identifier.lower() in row["nickname"].lower():
                await self._write_conn.execute(
                    "DELETE FROM blocks WHERE id = ?", (row["id"],)
                )
                await self._write_conn.commit()
                return True
        return False

    async def unblock_all(self, recipient_id: str) -> int:
        cur = await self._write_conn.execute(
            "DELETE FROM blocks WHERE recipient_id = ?", (int(recipient_id),)
        )
        await self._write_conn.commit()
        return cur.rowcount

    def is_blocked_by_user_id(
        self, recipient_id: str, blocked_user_id: int
    ) -> bool:
        cur = self._read_conn.execute(
            "SELECT 1 FROM blocks WHERE recipient_id = ? AND blocked_user_id = ?",
            (int(recipient_id), blocked_user_id),
        )
        return cur.fetchone() is not None

    def is_blocked(self, recipient_id: str, nickname: str) -> bool:
        cur = self._read_conn.execute(
            "SELECT 1 FROM blocks WHERE recipient_id = ? AND nickname = ?",
            (int(recipient_id), nickname),
        )
        return cur.fetchone() is not None

    def get_blocked_users(self, recipient_id: str) -> List[str]:
        rows = self._read_conn.execute(
            "SELECT nickname, special_code FROM blocks WHERE recipient_id = ?",
            (int(recipient_id),),
        ).fetchall()
        return [
            f"<b>{r['nickname']}</b> <code>{r['special_code'] or 'N/A'}</code>"
            for r in rows
        ]

    def get_blocked_count(self, recipient_id: str) -> int:
        cur = self._read_conn.execute(
            "SELECT COUNT(*) FROM blocks WHERE recipient_id = ?",
            (int(recipient_id),),
        )
        return cur.fetchone()[0]

    def is_user_blocked(self, recipient_id: str, identifier: str) -> bool:
        # Check special_code exact match
        cur = self._read_conn.execute(
            "SELECT 1 FROM blocks WHERE recipient_id = ? AND special_code = ?",
            (int(recipient_id), identifier),
        )
        if cur.fetchone():
            return True
        # Fallback: nickname substring
        rows = self._read_conn.execute(
            "SELECT nickname FROM blocks WHERE recipient_id = ?",
            (int(recipient_id),),
        ).fetchall()
        return any(identifier.lower() in r["nickname"].lower() for r in rows)

    def get_blocked_entry(
        self, recipient_id: str, identifier: str
    ) -> Optional[dict]:
        rows = self._read_conn.execute(
            "SELECT nickname, special_code FROM blocks WHERE recipient_id = ?",
            (int(recipient_id),),
        ).fetchall()
        for r in rows:
            if r["special_code"] == identifier or identifier.lower() in r["nickname"].lower():
                return {"nickname": r["nickname"], "special_code": r["special_code"]}
        return None

    # ---- Message Type Locking ----

    async def lock_type(self, user_id: str, msg_type: str) -> bool:
        row = self._read_conn.execute(
            "SELECT allowed_types FROM users WHERE telegram_id = ?",
            (int(user_id),),
        ).fetchone()
        if not row:
            return False
        allowed = json.loads(row["allowed_types"]) if row["allowed_types"] else []

        if msg_type == "all":
            new_allowed = [t for t in allowed if t == "text"]
            changed = len(new_allowed) != len(allowed)
        elif msg_type in self.VALID_TYPES and msg_type not in ("text", "all"):
            if msg_type not in allowed:
                return False
            new_allowed = [t for t in allowed if t != msg_type]
            changed = True
        else:
            return False

        if changed:
            await self._write_conn.execute(
                "UPDATE users SET allowed_types = ? WHERE telegram_id = ?",
                (json.dumps(new_allowed), int(user_id)),
            )
            await self._write_conn.commit()
        return changed

    async def unlock_type(self, user_id: str, msg_type: str) -> bool:
        row = self._read_conn.execute(
            "SELECT allowed_types FROM users WHERE telegram_id = ?",
            (int(user_id),),
        ).fetchone()
        if not row:
            return False
        allowed = json.loads(row["allowed_types"]) if row["allowed_types"] else []

        if msg_type == "all":
            new_allowed = list(allowed)
            changed = False
            for t in self.VALID_TYPES:
                if t not in ("text", "all") and t not in new_allowed:
                    new_allowed.append(t)
                    changed = True
        elif msg_type in self.VALID_TYPES and msg_type not in ("text", "all"):
            if msg_type in allowed:
                return False
            new_allowed = allowed + [msg_type]
            changed = True
        else:
            return False

        if changed:
            await self._write_conn.execute(
                "UPDATE users SET allowed_types = ? WHERE telegram_id = ?",
                (json.dumps(new_allowed), int(user_id)),
            )
            await self._write_conn.commit()
        return changed

    async def reset_allowed_types(self, user_id: str) -> bool:
        cur = await self._write_conn.execute(
            "UPDATE users SET allowed_types = ? WHERE telegram_id = ?",
            (json.dumps(self.DEFAULT_ALLOWED), int(user_id)),
        )
        await self._write_conn.commit()
        return cur.rowcount > 0

    def get_allowed_types(self, user_id: str) -> List[str]:
        cur = self._read_conn.execute(
            "SELECT allowed_types FROM users WHERE telegram_id = ?",
            (int(user_id),),
        )
        row = cur.fetchone()
        if not row or not row["allowed_types"]:
            return ["text"]
        return json.loads(row["allowed_types"])

    # ---- Pending Target ----

    async def set_pending_target(self, sender_id: int, target_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._write_conn.execute(
            """INSERT OR REPLACE INTO pending_targets
               (sender_id, target_id, created_at) VALUES (?, ?, ?)""",
            (sender_id, target_id, now),
        )
        await self._write_conn.execute(
            "UPDATE users SET last_activity = ? WHERE telegram_id = ?",
            (now, sender_id),
        )
        await self._write_conn.commit()

    def get_pending_target(self, sender_id: int) -> Optional[int]:
        cur = self._read_conn.execute(
            "SELECT target_id FROM pending_targets WHERE sender_id = ?",
            (sender_id,),
        )
        row = cur.fetchone()
        return row["target_id"] if row else None

    async def refresh_pending_target(self, sender_id: int) -> None:
        """Reset the inactivity timer for a pending target."""
        now = datetime.now(timezone.utc).isoformat()
        await self._write_conn.execute(
            "UPDATE pending_targets SET created_at = ? WHERE sender_id = ?",
            (now, sender_id),
        )
        await self._write_conn.commit()

    async def clear_pending_target(self, sender_id: int) -> None:
        await self._write_conn.execute(
            "DELETE FROM pending_targets WHERE sender_id = ?", (sender_id,)
        )
        await self._write_conn.commit()

    # ---- Message Tracking ----

    async def store_message(
        self, bot_msg_id: int, sender_id: int, receiver_id: int
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._write_conn.execute(
            """INSERT OR REPLACE INTO messages
               (bot_msg_id, sender_id, receiver_id, timestamp)
               VALUES (?, ?, ?, ?)""",
            (bot_msg_id, sender_id, receiver_id, now),
        )
        await self._write_conn.commit()

    def get_message_sender(self, bot_msg_id: int) -> Optional[int]:
        cur = self._read_conn.execute(
            "SELECT sender_id FROM messages WHERE bot_msg_id = ?",
            (bot_msg_id,),
        )
        row = cur.fetchone()
        return row["sender_id"] if row else None

    def get_message_data(self, bot_msg_id: int) -> Optional[Dict[str, Any]]:
        cur = self._read_conn.execute(
            "SELECT * FROM messages WHERE bot_msg_id = ?", (bot_msg_id,)
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "sender_id": str(row["sender_id"]),
            "receiver_id": str(row["receiver_id"]),
            "timestamp": row["timestamp"],
        }

    async def cleanup_old_messages(self, max_age_hours: int = 24) -> int:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        ).isoformat()
        cur = await self._write_conn.execute(
            "DELETE FROM messages WHERE timestamp < ?", (cutoff,)
        )
        await self._write_conn.commit()
        return cur.rowcount

    # ---- Legacy Support ----

    def get_session(self, sender_id: int) -> Optional[Dict[str, Any]]:
        target_id = self.get_pending_target(sender_id)
        if target_id:
            return {"target_id": str(target_id)}
        return None

    async def clear_session(self, sender_id: int) -> None:
        await self.clear_pending_target(sender_id)

    def get_connection(self, user_id: int) -> Optional[Dict[str, Any]]:
        return self.get_session(user_id)

    async def end_connection(self, user_id: int) -> None:
        await self.clear_session(user_id)

    # ---- Rate Limiting ----

    async def add_message_timestamp(self, user_id: int, target_id: int) -> None:
        now = datetime.now(timezone.utc)
        await self._write_conn.execute(
            "INSERT INTO message_timestamps (user_id, target_id, timestamp) VALUES (?, ?, ?)",
            (user_id, target_id, now.isoformat()),
        )
        # Clean up entries older than 1 minute
        cutoff = (now - timedelta(seconds=60)).isoformat()
        await self._write_conn.execute(
            "DELETE FROM message_timestamps WHERE user_id = ? AND target_id = ? AND timestamp < ?",
            (user_id, target_id, cutoff),
        )
        await self._write_conn.commit()

    def get_message_count_in_window(self, user_id: int, target_id: int) -> int:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=60)
        ).isoformat()
        cur = self._read_conn.execute(
            "SELECT COUNT(*) FROM message_timestamps WHERE user_id = ? AND target_id = ? AND timestamp >= ?",
            (user_id, target_id, cutoff),
        )
        return cur.fetchone()[0]

    # ---- Inactivity Check ----

    def get_expired_pending_targets(self, timeout_minutes: int = 5) -> List[tuple]:
        """Return list of (sender_id, target_id) for expired pending targets."""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)
        ).isoformat()
        rows = self._read_conn.execute(
            "SELECT sender_id, target_id FROM pending_targets WHERE created_at < ?",
            (cutoff,),
        ).fetchall()
        return [(r["sender_id"], r["target_id"]) for r in rows]

    async def cleanup_expired_pending_targets(
        self, timeout_minutes: int = 5
    ) -> List[tuple]:
        """Delete expired pending targets and return (sender_id, target_id) pairs."""
        expired = self.get_expired_pending_targets(timeout_minutes)
        if expired:
            sender_ids = [s for s, _ in expired]
            placeholders = ",".join("?" * len(sender_ids))
            await self._write_conn.execute(
                f"DELETE FROM pending_targets WHERE sender_id IN ({placeholders})",
                sender_ids,
            )
            await self._write_conn.commit()
        return expired

    # ---- Temporary Links ----

    async def create_temp_link(
        self,
        user_id: int,
        expires_days: Optional[int] = None,
        max_uses: Optional[int] = None,
    ) -> str:
        token = secrets.token_urlsafe(12)
        now = datetime.now(timezone.utc)
        expires_at = (
            (now + timedelta(days=expires_days)).isoformat()
            if expires_days
            else None
        )
        await self._write_conn.execute(
            """INSERT INTO temp_links
               (token, user_id, created_at, expires_at, max_uses)
               VALUES (?, ?, ?, ?, ?)""",
            (token, user_id, now.isoformat(), expires_at, max_uses),
        )
        await self._write_conn.commit()
        return token

    def get_temp_link(self, token: str) -> Optional[Dict[str, Any]]:
        cur = self._read_conn.execute(
            "SELECT * FROM temp_links WHERE token = ?", (token,)
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def get_user_by_temp_link(
        self, token: str
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]]]:
        link = self.get_temp_link(token)
        if not link or not link.get("active"):
            return None, None

        if link.get("expires_at"):
            try:
                expires_at = datetime.fromisoformat(link["expires_at"])
                if datetime.now(timezone.utc) > expires_at:
                    return None, None
            except ValueError:
                pass

        if link.get("max_uses") is not None:
            if (link.get("current_uses") or 0) >= link["max_uses"]:
                return None, None

        user_id = link["user_id"]
        user = self.get_user(user_id)
        return user_id, user

    async def use_temp_link(self, token: str) -> bool:
        cur = await self._write_conn.execute(
            "UPDATE temp_links SET current_uses = current_uses + 1 WHERE token = ? AND active = 1",
            (token,),
        )
        await self._write_conn.commit()
        return cur.rowcount > 0

    async def revoke_temp_link(self, token: str, user_id: int) -> bool:
        cur = await self._write_conn.execute(
            "UPDATE temp_links SET active = 0 WHERE token = ? AND user_id = ?",
            (token, user_id),
        )
        await self._write_conn.commit()
        return cur.rowcount > 0

    async def delete_temp_link(self, token: str, user_id: int) -> bool:
        cur = await self._write_conn.execute(
            "DELETE FROM temp_links WHERE token = ? AND user_id = ?",
            (token, user_id),
        )
        await self._write_conn.commit()
        return cur.rowcount > 0

    async def delete_all_temp_links(self, user_id: int) -> int:
        cur = await self._write_conn.execute(
            "DELETE FROM temp_links WHERE user_id = ?", (user_id,)
        )
        await self._write_conn.commit()
        return cur.rowcount

    def get_user_temp_links(self, user_id: int) -> List[Dict[str, Any]]:
        rows = self._read_conn.execute(
            "SELECT * FROM temp_links WHERE user_id = ?", (user_id,)
        ).fetchall()
        return [{"token": r["token"], **dict(r)} for r in rows]

    def get_active_temp_links(self, user_id: int) -> List[Dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        rows = self._read_conn.execute(
            "SELECT * FROM temp_links WHERE user_id = ? AND active = 1",
            (user_id,),
        ).fetchall()
        result = []
        for r in rows:
            link = dict(r)
            if link.get("expires_at"):
                try:
                    if now > link["expires_at"]:
                        continue
                except (ValueError, TypeError):
                    pass
            if link.get("max_uses") is not None:
                if (link.get("current_uses") or 0) >= link["max_uses"]:
                    continue
            link["token"] = r["token"]
            result.append(link)
        return result

    # ---- Webapp Messages ----

    async def store_webapp_message(
        self,
        sender_id: int,
        receiver_id: int,
        sender_nickname: str,
        message_text: str,
        message_type: str = "text",
    ) -> int:
        """Persist a message for the webapp inbox. Returns the new row id."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cur = await self._write_conn.execute(
            """INSERT INTO webapp_messages
               (sender_id, receiver_id, sender_nickname, message_text, message_type, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (sender_id, receiver_id, sender_nickname, message_text, message_type, now),
        )
        await self._write_conn.commit()
        return cur.lastrowid

    def get_inbox_messages(
        self, user_id: int, limit: int = 50, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Fetch inbox messages for a user, newest first."""
        rows = self._read_conn.execute(
            """SELECT id, sender_id, sender_nickname, message_text, message_type,
                      created_at, read
               FROM webapp_messages
               WHERE receiver_id = ?
               ORDER BY created_at DESC
               LIMIT ? OFFSET ?""",
            (user_id, limit, offset),
        ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            # Normalize dates for JS: +00:00 -> Z
            ca = d.get("created_at", "")
            if ca.endswith("+00:00"):
                d["created_at"] = ca[:-6] + "Z"
            results.append(d)
        return results

    async def mark_message_read(self, message_id: int, user_id: int) -> bool:
        """Mark a single inbox message as read."""
        cur = await self._write_conn.execute(
            "UPDATE webapp_messages SET read = 1 WHERE id = ? AND receiver_id = ?",
            (message_id, user_id),
        )
        await self._write_conn.commit()
        return cur.rowcount > 0

    def get_unread_count(self, user_id: int) -> int:
        """Count unread inbox messages for a user."""
        cur = self._read_conn.execute(
            "SELECT COUNT(*) FROM webapp_messages WHERE receiver_id = ? AND read = 0",
            (user_id,),
        )
        return cur.fetchone()[0]

    def get_dashboard_stats(self, user_id: int) -> Dict[str, Any]:
        """Get full dashboard statistics for mini app."""
        user = self.get_user(user_id)
        if not user:
            return {}

        now = datetime.now(timezone.utc)

        blocked_count = self.get_blocked_count(str(user_id))

        # Send raw ISO timestamps for frontend relative time computation
        def _normalize_iso(s: str) -> str:
            if s and not s.endswith("Z") and "+" not in s:
                return s + "+00:00"
            return s

        return {
            "nickname": user.get("nickname", ""),
            "lang": user.get("lang", "en"),
            "messages_sent": user.get("messages_sent", 0),
            "messages_received": user.get("messages_received", 0),
            "registered_at": _normalize_iso(user.get("registered_at", "")),
            "last_activity": _normalize_iso(user.get("last_activity", "")),
            "blocked_count": blocked_count,
            "revoke_count": user.get("revoke_count", 0),
            "link_token": user.get("token", ""),
            "avatar": user.get("avatar"),
            "frame": user.get("frame"),
            "profile_token": user.get("profile_token", ""),
            "profile_public": user.get("profile_public", False),
            "profile_show_last_seen": user.get("profile_show_last_seen", False),
            "profile_show_level": user.get("profile_show_level", True),
            "profile_show_active_days": user.get("profile_show_active_days", True),
            "profile_show_registered": user.get("profile_show_registered", True),
        }

    # ---- Profile ----

    def get_user_by_profile_token(self, profile_token: str) -> Optional[Dict[str, Any]]:
        """Look up a user by their profile_token. Returns user dict or None."""
        cur = self._read_conn.execute(
            "SELECT * FROM users WHERE profile_token = ?", (profile_token,)
        )
        row = cur.fetchone()
        return self._row_to_user_dict(row) if row else None

    async def set_profile_settings(self, telegram_id: int, settings: Dict[str, Any]) -> bool:
        """Update profile toggle settings. Only known keys are accepted."""
        allowed_keys = {
            "profile_public", "profile_show_last_seen", "profile_show_level",
            "profile_show_active_days", "profile_show_registered",
        }
        sets = []
        params = []
        for key, value in settings.items():
            if key in allowed_keys:
                sets.append(f"{key} = ?")
                params.append(int(bool(value)))
        if not sets:
            return False
        params.append(telegram_id)
        cur = await self._write_conn.execute(
            f"UPDATE users SET {', '.join(sets)} WHERE telegram_id = ?", params
        )
        await self._write_conn.commit()
        return cur.rowcount > 0

    async def cleanup_expired_temp_links(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        # Delete inactive links
        cur1 = await self._write_conn.execute(
            "DELETE FROM temp_links WHERE active = 0"
        )
        # Delete expired links
        cur2 = await self._write_conn.execute(
            "DELETE FROM temp_links WHERE expires_at IS NOT NULL AND expires_at < ?",
            (now,),
        )
        await self._write_conn.commit()
        return cur1.rowcount + cur2.rowcount
