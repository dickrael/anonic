"""Utility functions for token and nickname generation."""

import json
import os
import random
import string
import sys
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

NICKNAMES_FILE = os.path.join("assets", "nicknames.json")

_first_parts: List[str] = []
_second_parts: List[str] = []


def load_nicknames() -> None:
    """Load nickname parts from JSON file."""
    global _first_parts, _second_parts
    try:
        with open(NICKNAMES_FILE, 'r') as f:
            nick_data = json.load(f)
        _first_parts = nick_data.get("first_parts", [])
        _second_parts = nick_data.get("second_parts", [])
        logger.info(f"Loaded {len(_first_parts)} first parts and {len(_second_parts)} second parts for nicknames")
    except Exception as e:
        sys.stderr.write(f"Failed to load nicknames JSON: {e}\n")
        sys.exit(1)


def generate_token() -> str:
    """Generate a random token for user identification."""
    salt = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    return random.choice(string.ascii_letters) + salt


def generate_nickname() -> str:
    """Generate a random nickname from parts."""
    first = random.choice(_first_parts) if _first_parts else "Anon"
    second = random.choice(_second_parts) if _second_parts else "User"
    return f"{first} {second}"


def extract_nickname_from_message(text: str) -> Optional[str]:
    """Extract sender nickname from message text.

    Handles two formats:
    1. '✅ ... <b>Nickname</b>' - confirmation format
    2. '... –– <b>Nickname</b>' - message caption format
    """
    if not text:
        return None

    sender_nickname = None

    if text.startswith("✅"):
        lines_parts = text.split('\n')
        for line in lines_parts:
            if line.startswith("✅"):
                parts = line.split(" ")
                if len(parts) >= 4:
                    sender_nickname = " ".join(parts[3:]).replace('<b>', '').replace('</b>', '')
                    break
    elif '–– ' in text:
        sender_nickname_block = text.split('–– ')[-1]
        sender_nickname_line = sender_nickname_block.split('\n')[0].strip()
        sender_nickname = sender_nickname_line.replace('<b>', '').replace('</b>', '')

    return sender_nickname.strip() if sender_nickname else None
