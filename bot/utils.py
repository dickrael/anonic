"""Utility functions for token and nickname generation."""

import json
import os
import random
import secrets
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


def generate_profile_token() -> str:
    """Generate a URL-safe profile token (~12 chars, independent from link token)."""
    return secrets.token_urlsafe(9)


def generate_nickname() -> str:
    """Generate a random nickname from parts."""
    first = random.choice(_first_parts) if _first_parts else "Anon"
    second = random.choice(_second_parts) if _second_parts else "User"
    return f"{first} {second}"


def extract_nickname_from_message(text: str) -> Optional[str]:
    """Extract sender nickname from message text.

    Handles formats:
    1. '✅ Message sent to <b>Nickname</b>' - sent confirmation
    2. '✅ Connection established with <b>Nickname</b>' - connection confirmation
    3. '... –– <b>Nickname</b>' - message caption format
    """
    if not text:
        return None

    # Clean HTML tags for easier parsing
    clean_text = text.replace('<b>', '').replace('</b>', '').replace('<code>', '').replace('</code>', '')

    # Format: "✅ Message sent to Nickname"
    if 'sent to ' in clean_text:
        after_sent = clean_text.split('sent to ')[-1]
        nickname = after_sent.split('\n')[0].strip()
        if nickname:
            return nickname

    # Format: "✅ Connection established with Nickname"
    if 'established with ' in clean_text:
        after_with = clean_text.split('established with ')[-1]
        nickname = after_with.split('.')[0].split('\n')[0].strip()
        if nickname:
            return nickname

    # Format: "text\n–– Nickname"
    if '–– ' in clean_text:
        after_dash = clean_text.split('–– ')[-1]
        nickname = after_dash.split('\n')[0].strip()
        if nickname:
            return nickname

    return None
