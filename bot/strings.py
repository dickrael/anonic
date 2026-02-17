"""Language string management."""

import logging
import os
import re
from typing import Dict, Any, Optional

import yaml

_TAG_RE = re.compile(r"<[^>]+>")

from pyrogram.types import Message

logger = logging.getLogger(__name__)


class Strings:
    """Manages localized strings from YAML files."""

    def __init__(self, langs_dir: str = "langs"):
        self.langs_dir = langs_dir
        self.strings: Dict[str, Dict[str, str]] = {}
        self._store_getter = None
        self.reload_strings()

    def set_store_getter(self, getter) -> None:
        """Set the function to get store instance (avoids circular imports)."""
        self._store_getter = getter

    def reload_strings(self) -> None:
        """Load language strings from YAML files."""
        os.makedirs(self.langs_dir, exist_ok=True)
        for file in os.listdir(self.langs_dir):
            if file.endswith(".yml"):
                lang_code = file[:-4]
                try:
                    with open(os.path.join(self.langs_dir, file), "r", encoding="utf-8") as f:
                        self.strings[lang_code] = yaml.safe_load(f) or {}
                    logger.info(f"Loaded strings for {lang_code}")
                except Exception as e:
                    logger.error(f"Failed to load strings for {lang_code}: {e}")
        logger.info(f"Languages loaded: {list(self.strings.keys())}")

    def get_available_languages(self) -> list:
        """Get list of available language codes."""
        return list(self.strings.keys())

    def get_raw(self, key: str, lang: str = "en") -> str:
        """Get raw string by key and language."""
        result = self.strings.get(lang, {}).get(key)
        if result is None:
            # Fallback to English
            result = self.strings.get("en", {}).get(key)
        if result is None:
            logger.warning(f"Missing string '{key}' in language '{lang}'")
            return f"Missing string: {key}"
        return result

    async def get(
        self,
        key: str,
        message: Optional[Message] = None,
        user_id: Optional[int] = None
    ) -> str:
        """Get localized string based on user's language.

        Args:
            key: The string key to look up
            message: Optional Message object to get user_id from
            user_id: Optional user ID to look up language for

        Returns:
            Localized string
        """
        if message and user_id:
            raise ValueError("Provide either message or user_id, not both")
        if not message and not user_id:
            raise ValueError("Either message or user_id must be provided")

        if message:
            user_id = message.from_user.id

        lang = "en"
        if self._store_getter:
            store = self._store_getter()
            lang = store.get_user_language(user_id)

        return self.get_raw(key, lang)


# Global strings instance
strings = Strings()


async def gstr(key: str, message: Optional[Message] = None, user_id: Optional[int] = None) -> str:
    """Convenience function to get localized string."""
    return await strings.get(key, message, user_id)


def plain(text: str) -> str:
    """Strip all HTML/emoji tags for use in callback.answer() alerts."""
    return _TAG_RE.sub("", text).strip()
