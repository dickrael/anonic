"""Bot command and message handlers."""

from .start import register_start_handlers
from .disconnect import register_disconnect_handlers
from .messaging import register_messaging_handlers
from .blocking import register_blocking_handlers
from .lock_types import register_lock_handlers
from .moderation import register_moderation_handlers
from .language import register_language_handlers
from .help import register_help_handlers
from .security import register_security_handlers
from .stats import register_stats_handlers
from .temp_links import register_temp_links_handlers
from .restart import register_restart_handlers


def register_all_handlers(app) -> None:
    """Register all handlers with the app.

    Order matters: command handlers MUST be registered before messaging_handlers
    since messaging is the catch-all handler.
    """
    # Command handlers first
    register_start_handlers(app)
    register_restart_handlers(app)
    register_disconnect_handlers(app)
    register_help_handlers(app)
    register_security_handlers(app)
    register_stats_handlers(app)
    register_temp_links_handlers(app)
    register_blocking_handlers(app)
    register_lock_handlers(app)
    register_moderation_handlers(app)
    register_language_handlers(app)
    # Messaging handler LAST (catch-all for anonymous messages)
    register_messaging_handlers(app)
