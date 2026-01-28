"""Inactivity scheduler for automatic disconnection."""

import logging
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pyrogram.errors import UserIsBlocked, InputUserDeactivated
from pyrogram.enums import ParseMode

if TYPE_CHECKING:
    from pyrogram import Client
    from .store import JSONStore

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def check_inactive_users(app: "Client", store: "JSONStore", strings_dict: dict) -> None:
    """Check and disconnect inactive users.

    Args:
        app: Pyrogram client instance
        store: Data store instance
        strings_dict: Language strings dictionary
    """
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
            await store.end_connection(uid)
            logger.info(f"User {uid} disconnected from {target_id}: target not found")
            continue

        # Check if blocked
        if (target.get('banned', False) or
            store.is_blocked(str(uid), target['nickname']) or
            store.is_blocked(str(target_id), user_data['nickname'])):
            await store.end_connection(uid)
            logger.info(f"User {uid} disconnected from {target_id}: banned or blocked")
            continue

        try:
            await app.get_chat(target_id)

            # Only notify if messages were exchanged
            message_count = store.get_message_count(uid)
            if message_count > 0:
                user_lang = store.get_user_language(uid)
                target_lang = store.get_user_language(target_id)

                user_msg = strings_dict.get(user_lang, strings_dict['en']).get(
                    'inactivity_disconnect', ''
                ).format(nickname=target['nickname'])

                target_msg = strings_dict.get(target_lang, strings_dict['en']).get(
                    'inactivity_disconnect', ''
                ).format(nickname=user_data['nickname'])

                try:
                    await app.send_message(uid, user_msg, parse_mode=ParseMode.HTML)
                except Exception as e:
                    logger.error(f"Failed to notify user {uid}: {type(e).__name__}: {e}")

                try:
                    await app.send_message(target_id, target_msg, parse_mode=ParseMode.HTML)
                except Exception as e:
                    logger.error(f"Failed to notify target {target_id}: {type(e).__name__}: {e}")

            await store.end_connection(uid)
            logger.info(
                f"Users {uid} ({user_data['nickname']}) and {target_id} ({target['nickname']}) "
                f"disconnected due to inactivity"
            )

        except UserIsBlocked:
            await store.end_connection(uid)
            logger.info(f"User {uid} disconnected from {target_id}: UserIsBlocked")
        except InputUserDeactivated:
            await store.end_connection(uid)
            logger.info(f"User {uid} disconnected from {target_id}: InputUserDeactivated")
        except Exception as e:
            logger.error(f"Error checking inactive user {uid}: {type(e).__name__}: {e}")


def start_scheduler(app: "Client", store: "JSONStore", strings_dict: dict) -> None:
    """Start the inactivity check scheduler."""
    scheduler.add_job(
        check_inactive_users,
        'interval',
        minutes=1,
        args=[app, store, strings_dict]
    )
    scheduler.start()
    logger.info("Inactivity scheduler started")


def stop_scheduler() -> None:
    """Stop the scheduler."""
    scheduler.shutdown()
    logger.info("Inactivity scheduler stopped")
