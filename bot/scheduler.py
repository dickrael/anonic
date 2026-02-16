"""Scheduler for cleanup tasks."""

import logging
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pyrogram.enums import ParseMode

if TYPE_CHECKING:
    from pyrogram import Client
    from .store import SQLiteStore

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()
_client: "Client | None" = None


async def cleanup_expired_pending_targets(store: "SQLiteStore") -> None:
    """Clean up pending targets that weren't used within timeout, and notify users."""
    from .strings import gstr
    try:
        expired = await store.cleanup_expired_pending_targets(timeout_minutes=5)
        if expired:
            logger.info(f"Cleaned up {len(expired)} expired pending targets")
        for sender_id, target_id in expired:
            try:
                target_data = store.get_user(target_id)
                target_nick = target_data.get("nickname", "???") if target_data else "???"
                sender_data = store.get_user(sender_id)
                sender_nick = sender_data.get("nickname", "???") if sender_data else "???"
                # Notify sender
                msg = (await gstr("inactivity_disconnect", user_id=sender_id)).format(nickname=target_nick)
                await _client.send_message(sender_id, msg, parse_mode=ParseMode.HTML)
                # Notify target
                msg = (await gstr("inactivity_disconnect", user_id=target_id)).format(nickname=sender_nick)
                await _client.send_message(target_id, msg, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.warning(f"Failed to notify user about inactivity disconnect: {e}")
    except Exception as e:
        logger.error(f"Error cleaning up pending targets: {type(e).__name__}: {e}")


async def cleanup_old_messages(store: "SQLiteStore") -> None:
    """Clean up old message tracking entries."""
    try:
        deleted = await store.cleanup_old_messages(max_age_hours=24)
        if deleted > 0:
            logger.info(f"Cleaned up {deleted} old message tracking entries")
    except Exception as e:
        logger.error(f"Error cleaning up old messages: {type(e).__name__}: {e}")


def start_scheduler(app: "Client", store: "SQLiteStore", strings_dict: dict) -> None:
    """Start the cleanup schedulers."""
    global _client
    _client = app
    # Cleanup expired pending targets every minute
    scheduler.add_job(
        cleanup_expired_pending_targets,
        'interval',
        minutes=1,
        args=[store]
    )

    # Cleanup old message tracking every hour
    scheduler.add_job(
        cleanup_old_messages,
        'interval',
        hours=1,
        args=[store]
    )

    scheduler.start()
    logger.info("Schedulers started (pending target cleanup + message cleanup)")


def stop_scheduler() -> None:
    """Stop the scheduler."""
    scheduler.shutdown()
    logger.info("Schedulers stopped")
