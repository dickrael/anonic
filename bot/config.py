"""Configuration loading and validation."""

import os
import sys
from dataclasses import dataclass
from dotenv import load_dotenv


@dataclass
class Config:
    """Bot configuration."""
    api_id: int
    api_hash: str
    bot_token: str
    moderation_chat_id: str
    owner_id: int
    data_file: str


def load_config() -> Config:
    """Load and validate configuration from environment variables."""
    load_dotenv()

    api_id_str = os.getenv("API_ID")
    api_hash = os.getenv("API_HASH")
    bot_token = os.getenv("BOT_TOKEN")
    moderation_chat_id = os.getenv("MODERATION_CHAT_ID")
    owner_id_str = os.getenv("OWNER_ID")
    data_file = os.getenv("DATA_FILE", "data.json")

    missing = []
    if not api_id_str:
        missing.append("API_ID")
    if not api_hash:
        missing.append("API_HASH")
    if not bot_token:
        missing.append("BOT_TOKEN")
    if not moderation_chat_id:
        missing.append("MODERATION_CHAT_ID")
    if not owner_id_str:
        missing.append("OWNER_ID")

    if missing:
        sys.stderr.write(f"Missing required environment variables: {', '.join(missing)}\n")
        sys.exit(1)

    try:
        api_id = int(api_id_str)
        owner_id = int(owner_id_str)
    except ValueError:
        sys.stderr.write("API_ID and OWNER_ID must be integers.\n")
        sys.exit(1)

    return Config(
        api_id=api_id,
        api_hash=api_hash,
        bot_token=bot_token,
        moderation_chat_id=moderation_chat_id,
        owner_id=owner_id,
        data_file=data_file,
    )


config = load_config()
