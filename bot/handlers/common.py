"""Common utilities for handlers."""

import logging
from typing import Tuple

from pyrogram import Client
from pyrogram.errors import (
    UserIsBlocked, InputUserDeactivated, PeerIdInvalid,
    UserDeactivated, UserDeactivatedBan
)

from ..store import get_store

logger = logging.getLogger(__name__)

# Frozen participant error messages
FROZEN_ERRORS = ["FROZEN_PARTICIPANT_MISSING", "USER_DEACTIVATED", "USER_DEACTIVATED_BAN"]


async def can_connect(client: Client, user_id: int, target_id: int, check_busy: bool = False) -> Tuple[bool, str]:
    """Check if a message can be sent to the target user.

    Args:
        client: Pyrogram client
        user_id: ID of user initiating message
        target_id: ID of target user
        check_busy: Ignored (kept for backward compatibility)

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

    # Check if target blocked sender (by user_id to prevent revoke bypass)
    if store.is_blocked_by_user_id(str(target_id), user_id):
        return False, "blocked"

    # Check if sender blocked target (self_blocked means "you blocked them")
    if store.is_blocked_by_user_id(str(user_id), target_id):
        return False, "self_blocked"

    try:
        await client.get_chat(target_id)
        return True, ""
    except UserIsBlocked:
        return False, "blocked"
    except InputUserDeactivated:
        return False, "deactivated"
    except (UserDeactivated, UserDeactivatedBan):
        return False, "deactivated"
    except PeerIdInvalid:
        return False, "invalid_peer"
    except Exception as e:
        error_msg = str(e)
        # Check for frozen participant error
        if any(err in error_msg for err in FROZEN_ERRORS):
            return False, "frozen"
        logger.error(f"Failed to check connection for {target_id}: {type(e).__name__}: {e}")
        return False, "invalid_peer"
