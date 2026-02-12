"""Scheduler for cleanup tasks."""

import logging
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler

if TYPE_CHECKING:
    from pyrogram import Client
    from .store import SQLiteStore

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def cleanup_expired_pending_targets(store: "SQLiteStore") -> None:
    """Clean up pending targets that weren't used within timeout."""
    try:
        deleted = await store.cleanup_expired_pending_targets(timeout_minutes=5)
        if deleted > 0:
            logger.info(f"Cleaned up {deleted} expired pending targets")
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
