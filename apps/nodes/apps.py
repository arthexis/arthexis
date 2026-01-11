import logging
from django.apps import AppConfig

from apps.celery.utils import schedule_task

logger = logging.getLogger(__name__)


class NodesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.nodes"
    label = "nodes"

    def ready(self):  # pragma: no cover - exercised on app start
        # Import node signal handlers
        from . import signals  # noqa: F401

        try:
            from .tasks import send_startup_net_message

            schedule_task(
                send_startup_net_message,
                countdown=0,
                require_enabled=True,
            )
        except Exception:
            logger.exception("Failed to enqueue LCD startup message")
