"""Bot command and message handlers."""

from .start import register_start_handlers
from .disconnect import register_disconnect_handlers
from .messaging import register_messaging_handlers
from .blocking import register_blocking_handlers
from .lock_types import register_lock_handlers
from .moderation import register_moderation_handlers
from .language import register_language_handlers
from .help import register_help_handlers


def register_all_handlers(app) -> None:
    """Register all handlers with the app."""
    register_start_handlers(app)
    register_disconnect_handlers(app)
    register_help_handlers(app)
    register_messaging_handlers(app)
    register_blocking_handlers(app)
    register_lock_handlers(app)
    register_moderation_handlers(app)
    register_language_handlers(app)
