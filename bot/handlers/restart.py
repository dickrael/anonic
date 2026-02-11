"""Owner-only restart handler with git pull."""

import os
import sys
import asyncio
import logging

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ParseMode

from ..config import config

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
