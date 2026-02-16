"""Bot entry point."""

import asyncio
import logging
import signal
import sys

import uvicorn
from pyrogram.types import BotCommand

from .config import config
from .client import create_client, get_client
from .store import init_store, get_store
from .strings import strings
from .utils import load_nicknames
from .scheduler import start_scheduler, stop_scheduler
from .handlers import register_all_handlers
from .webapp import app as webapp_app


# Bot commands for the Telegram menu
BOT_COMMANDS = [
    BotCommand("start", "Get your anonymous link"),
    BotCommand("help", "How to use the bot"),
    BotCommand("stats", "Your statistics"),
    BotCommand("disconnect", "End current chat"),
    BotCommand("block", "Block sender (reply)"),
    BotCommand("unblock", "Unblock by code"),
    BotCommand("unblockall", "Unblock all users"),
    BotCommand("blocked", "View blocked users"),
    BotCommand("security", "Message protection"),
    BotCommand("temp_link", "Create temporary link"),
    BotCommand("activelinks", "Manage temp links"),
    BotCommand("revoke", "Get new identity"),
    BotCommand("locktypes", "Message type settings"),
    BotCommand("report", "Report message (reply)"),
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

# Ensure uvicorn logs show in terminal
logging.getLogger("uvicorn").setLevel(logging.INFO)
logging.getLogger("uvicorn.error").setLevel(logging.INFO)
logging.getLogger("uvicorn.access").setLevel(logging.INFO)


async def init_bot() -> None:
    """Initialize and start the bot."""
    # Load nicknames
    load_nicknames()

    # Initialize store (async — creates tables)
    store = await init_store(config.data_file)

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

            # Start FastAPI webapp server (disable signal handlers to not interfere with pyrogram)
            uvicorn_config = uvicorn.Config(
                webapp_app,
                host="0.0.0.0",
                port=config.webapp_port,
                log_level="info",
                access_log=True,
            )
            webapp_server = uvicorn.Server(uvicorn_config)
            webapp_server.install_signal_handlers = lambda: None
            webapp_task = asyncio.create_task(webapp_server.serve())
            logger.info(f"WebApp API server started on port {config.webapp_port}")

            break
        except Exception as e:
            logger.error(f"Bot start failed: {type(e).__name__}: {e} | Attempt {attempt}")
            attempt += 1
            await asyncio.sleep(2)

    # Wait for stop signal (Ctrl+C / SIGTERM)
    stop_event = asyncio.Event()

    def _signal_handler(*_):
        logger.info("Stop signal received. Shutting down...")
        stop_event.set()

    loop = asyncio.get_event_loop()
    if sys.platform != "win32":
        loop.add_signal_handler(signal.SIGINT, _signal_handler)
        loop.add_signal_handler(signal.SIGTERM, _signal_handler)
    else:
        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

    await stop_event.wait()

    # Cleanup
    webapp_server.should_exit = True
    try:
        await asyncio.wait_for(webapp_task, timeout=3)
    except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
        webapp_task.cancel()
    logger.info("WebApp server stopped")

    if app.is_connected:
        await app.stop()
    stop_scheduler()
    logger.info("Bot stopped cleanly.")


def main() -> None:
    """Main entry point."""
    try:
        asyncio.get_event_loop().run_until_complete(init_bot())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
