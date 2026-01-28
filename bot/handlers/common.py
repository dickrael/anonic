"""Common utilities for handlers."""

import logging
from typing import Tuple

from pyrogram import Client
from pyrogram.errors import UserIsBlocked, InputUserDeactivated, PeerIdInvalid

from ..store import get_store

logger = logging.getLogger(__name__)


async def can_connect(client: Client, user_id: int, target_id: int) -> Tuple[bool, str]:
    """Check if a connection can be established between two users.

    Args:
        client: Pyrogram client
        user_id: ID of user initiating connection
        target_id: ID of target user

    Returns:
        Tuple of (success, reason) where reason is empty on success
    """
    store = get_store()
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
        logger.error(f"Failed to check connection for {target_id}: {type(e).__name__}: {e}")
        return False, "invalid_peer"
