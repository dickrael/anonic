"""Owner-only restart and profile update handlers."""

import os
import sys
import asyncio
import logging

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ParseMode

from ..config import config
from ..strings import strings

logger = logging.getLogger(__name__)


def register_restart_handlers(app: Client) -> None:
    """Register restart command handler."""

    @app.on_message(filters.command("re") & filters.private)
    async def restart_cmd(client: Client, message: Message):
        uid = message.from_user.id

        if uid != config.owner_id:
            return

        status_msg = await message.reply("🔄 Pulling updates...", parse_mode=ParseMode.HTML)

        # Git pull
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "pull", "--ff-only",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            git_output = stdout.decode().strip() or stderr.decode().strip()
        except asyncio.TimeoutError:
            git_output = "⚠️ git pull timed out"
        except Exception as e:
            git_output = f"⚠️ git pull failed: {e}"

        await status_msg.edit_text(
            f"<b>Git:</b> <code>{git_output}</code>\n\n🔄 Restarting...",
            parse_mode=ParseMode.HTML,
        )

        logger.info(f"Owner {uid} triggered restart. Git: {git_output}")

        # Schedule restart outside the handler to avoid "Task cannot await on itself"
        async def _do_restart():
            await asyncio.sleep(1)
            os.execv(sys.executable, [sys.executable, "-m", "bot"])

        asyncio.get_event_loop().create_task(_do_restart())

    @app.on_message(filters.command("pr_update") & filters.private)
    async def pr_update_cmd(client: Client, message: Message):
        """Update bot profile: name, description, short description for all languages."""
        uid = message.from_user.id

        if uid != config.owner_id:
            return

        status_msg = await message.reply("🔄 Updating bot profile...", parse_mode=ParseMode.HTML)

        updated = []
        failed = []
        for lang_code in strings.get_available_languages():
            lang_data = strings.strings.get(lang_code, {})
            name = lang_data.get("bot_name", "")
            desc = lang_data.get("bot_description", "")
            short_desc = lang_data.get("bot_short_description", "")
            lc = "" if lang_code == "en" else lang_code
            try:
                if name:
                    await client.set_bot_name(name, language_code=lc)
                if desc:
                    await client.set_bot_info_description(desc, language_code=lc)
                if short_desc:
                    await client.set_bot_info_short_description(short_desc, language_code=lc)
                updated.append(lang_code)
            except Exception as e:
                failed.append(f"{lang_code}: {e}")

        result = f"<b>Updated:</b> {', '.join(updated) or 'none'}"
        if failed:
            result += f"\n<b>Failed:</b>\n" + "\n".join(failed)

        await status_msg.edit_text(
            f"✅ Bot profile updated\n\n{result}",
            parse_mode=ParseMode.HTML,
        )
        logger.info(f"Owner {uid} updated bot profile: {updated}")
