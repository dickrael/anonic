"""Bot entry point."""

import asyncio
import logging

from pyrogram import idle
from pyrogram.types import BotCommand

from .config import config
from .client import create_client, get_client
from .store import init_store, get_store
from .strings import strings
from .utils import load_nicknames
from .scheduler import start_scheduler, stop_scheduler
from .handlers import register_all_handlers


# Bot commands for the Telegram menu
BOT_COMMANDS = [
    BotCommand("start", "Get your anonymous link"),
    BotCommand("disconnect", "End current conversation"),
    BotCommand("block", "Block a user"),
    BotCommand("unblock", "Unblock a user"),
    BotCommand("blocked", "List blocked users"),
    BotCommand("revoke", "Get new identity"),
    BotCommand("locktypes", "Message type settings"),
    BotCommand("report", "Report a message"),
    BotCommand("lang", "Change language"),
]

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Suppress APScheduler INFO logs
logging.getLogger("apscheduler").setLevel(logging.WARNING)


async def init_bot() -> None:
    """Initialize and start the bot."""
    # Load nicknames
    load_nicknames()

    # Initialize store
    store = init_store(config.data_file)

    # Set up strings to use store for language lookup
    strings.set_store_getter(get_store)

    # Create client
    app = create_client()

    # Register handlers
    register_all_handlers(app)

    attempt = 1
    while True:
        try:
            await app.start()
            bot_info = await app.get_me()
            logger.info(
                f"Bot '{bot_info.first_name}' (ID: {bot_info.id}) started"
                + (f" after {attempt} attempts" if attempt > 1 else "")
            )

            # Set bot commands menu
            await app.set_bot_commands(BOT_COMMANDS)
            logger.info("Bot commands menu set")

            # Start scheduler
            start_scheduler(app, store, strings.strings)

            break
        except Exception as e:
            logger.error(f"Bot start failed: {type(e).__name__}: {e} | Attempt {attempt}")
            attempt += 1
            await asyncio.sleep(2)

    try:
        await idle()
    except KeyboardInterrupt:
        logger.info("Stop signal received. Shutting down...")
    finally:
        if app.is_connected:
            await app.stop()
        stop_scheduler()
        logger.info("Bot stopped cleanly.")


def main() -> None:
    """Main entry point."""
    asyncio.get_event_loop().run_until_complete(init_bot())


if __name__ == "__main__":
    main()
