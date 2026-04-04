import logging
import os
import sys

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class NodesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.nodes"
    label = "nodes"
    _ASGI_SERVER_EXECUTABLES = ("daphne", "gunicorn", "hypercorn", "uvicorn")

    def _should_enqueue_startup_message(self) -> bool:
        argv = sys.argv
        if not argv:
            return False
        executable = os.path.basename(argv[0])
        command_args = argv[1:]
        if any(executable.startswith(server) for server in self._ASGI_SERVER_EXECUTABLES):
            return True
        if executable != "manage.py":
            if executable.startswith("python") and command_args:
                executable = os.path.basename(command_args[0])
                command_args = command_args[1:]
            else:
                return False
        if executable != "manage.py":
            return False
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

            send_startup_net_message(port=os.environ.get("PORT"))
        except Exception:
            logger.exception("Failed to send LCD startup message")
