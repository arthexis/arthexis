import logging
import os
import socket
from pathlib import Path

from django.apps import AppConfig
from django.conf import settings

from nodes.startup_notifications import lcd_feature_enabled, queue_startup_message

logger = logging.getLogger(__name__)


def _startup_notification(
    port: str | None = None, lock_file: Path | None = None, *, allow_db_lookup: bool = True
) -> None:
    base_dir = Path(getattr(settings, "BASE_DIR", Path(__file__).resolve().parents[1]))
    lock_dir = (lock_file.parent if lock_file else base_dir / "locks").resolve()
    if not lcd_feature_enabled(lock_dir):
        return

    port_value = port or os.environ.get("PORT", "8888")
    try:
        queue_startup_message(
            base_dir=base_dir,
            port=port_value,
            lock_file=lock_file,
            allow_db_lookup=allow_db_lookup,
        )
    except Exception:
        logger.exception("Failed to queue startup Net Message")


class NodesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "nodes"
    order = 4
    verbose_name = "Infrastructure"

    def ready(self):  # pragma: no cover - exercised on app start
        # Import signal handlers for content classifiers
        from . import signals  # noqa: F401

        try:
            _startup_notification(allow_db_lookup=False)
        except Exception:
            logger.exception("Failed to enqueue LCD startup message")
