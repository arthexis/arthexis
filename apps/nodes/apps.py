import logging
import os
import sys
from django.apps import AppConfig

from apps.celery.utils import schedule_task

logger = logging.getLogger(__name__)


class NodesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.nodes"
    label = "nodes"

    def _should_enqueue_startup_message(self) -> bool:
        argv = sys.argv
        if not argv:
            return True
        executable = os.path.basename(argv[0])
        command_args = argv[1:]
        if executable != "manage.py":
            if executable.startswith("python") and command_args:
                executable = os.path.basename(command_args[0])
                command_args = command_args[1:]
            else:
                return True
        if executable != "manage.py":
            return True
        return any(arg == "runserver" for arg in command_args)

    def ready(self):  # pragma: no cover - exercised on app start
        # Import node signal handlers
        from . import signals  # noqa: F401

        if not self._should_enqueue_startup_message():
            return

        if "runserver" in sys.argv and os.environ.get("RUN_MAIN") != "true":
            return

        try:
            from .tasks import send_startup_net_message

            schedule_task(
                send_startup_net_message,
                countdown=0,
                require_enabled=True,
            )
        except Exception:
            logger.exception("Failed to enqueue LCD startup message")
