"""Pyrogram client setup and management."""

import logging
from typing import Optional

from pyrogram import Client

from .config import config

logger = logging.getLogger(__name__)

# Global client instance
app: Optional[Client] = None


def create_client() -> Client:
    """Create and return the Pyrogram client."""
    global app
    app = Client(
        "anon_bot",
        api_id=config.api_id,
        api_hash=config.api_hash,
        bot_token=config.bot_token
    )
    return app


def get_client() -> Client:
    """Get the global client instance."""
    if app is None:
        raise RuntimeError("Client not initialized. Call create_client() first.")
    return app
