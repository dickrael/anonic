"""Stats command handlers."""

import logging
from datetime import datetime, timezone

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ParseMode

from ..store import get_store
from ..strings import gstr
from ..config import config

logger = logging.getLogger(__name__)


def format_date(iso_date: str) -> str:
    """Format ISO date to readable format."""
    if not iso_date:
        return "N/A"
    try:
        dt = datetime.fromisoformat(iso_date)
        return dt.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return "N/A"


def time_ago(iso_date: str) -> str:
    """Get human-readable time ago string."""
    if not iso_date:
        return "N/A"
    try:
        dt = datetime.fromisoformat(iso_date)
        now = datetime.now(timezone.utc)
        diff = now - dt

        seconds = diff.total_seconds()
        if seconds < 60:
            return "just now"
        elif seconds < 3600:
            minutes = int(seconds / 60)
            return f"{minutes}m ago"
        elif seconds < 86400:
            hours = int(seconds / 3600)
            return f"{hours}h ago"
        else:
            days = int(seconds / 86400)
            return f"{days}d ago"
    except ValueError:
        return "N/A"


def register_stats_handlers(app: Client) -> None:
    """Register stats command handlers."""

    @app.on_message(filters.command("stats") & filters.private)
    async def stats_cmd(client: Client, message: Message):
        store = get_store()
        uid = message.from_user.id

        if store.is_banned(uid):
            await message.reply(await gstr("banned", message), parse_mode=ParseMode.HTML)
            return

        user = store.get_user(uid)
        if not user:
            await message.reply(await gstr("stats_no_user", message), parse_mode=ParseMode.HTML)
            return

        stats = store.get_user_stats(uid)
        temp_links = store.get_active_temp_links(uid)

        await message.reply(
            (await gstr("stats_message", message)).format(
                messages_sent=stats.get('messages_sent', 0),
                messages_received=stats.get('messages_received', 0),
                total_messages=stats.get('messages_sent', 0) + stats.get('messages_received', 0),
                blocked_count=stats.get('blocked_count', 0),
                revoke_count=stats.get('revoke_count', 0),
                registered_at=format_date(stats.get('registered_at')),
                last_activity=time_ago(stats.get('last_activity')),
                protect_content="✅" if stats.get('protect_content') else "❌",
                temp_links_count=len(temp_links)
            ),
            parse_mode=ParseMode.HTML
        )
        logger.info(f"User {uid} viewed stats")

    @app.on_message(filters.command("adminstats") & filters.private)
    async def adminstats_cmd(client: Client, message: Message):
        store = get_store()
        uid = message.from_user.id

        if uid != config.owner_id:
            return  # Silently ignore for non-owners

        stats = store.get_admin_stats()

        await message.reply(
            (await gstr("admin_stats_message", message)).format(
                total_users=stats.get('total_users', 0),
                active_24h=stats.get('active_24h', 0),
                active_7d=stats.get('active_7d', 0),
                total_messages=stats.get('total_messages', 0),
                total_banned=stats.get('total_banned', 0),
                temp_links_count=stats.get('temp_links_count', 0)
            ),
            parse_mode=ParseMode.HTML
        )
        logger.info(f"Owner {uid} viewed admin stats")
