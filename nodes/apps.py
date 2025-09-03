import logging
import os
import socket
import threading
import time
from pathlib import Path

from django.apps import AppConfig
from django.conf import settings
from django.core.signals import request_started
from django.db import connections
from django.db.utils import OperationalError
from utils import revision


logger = logging.getLogger(__name__)


def _startup_notification() -> None:
    """Queue a notification with host:port and version on a background thread."""

    host = socket.gethostname()
    try:
        address = socket.gethostbyname(host)
    except socket.gaierror:
        address = host

    port = os.environ.get("PORT", "8000")

    version = ""
    ver_path = Path(settings.BASE_DIR) / "VERSION"
    if ver_path.exists():
        version = ver_path.read_text().strip()

    revision_value = revision.get_revision()
    rev_short = revision_value[-6:] if revision_value else ""

    body = f"v{version}"
    if rev_short:
        body += f" r{rev_short}"

    def _worker() -> None:  # pragma: no cover - background thread
        # Allow the LCD a moment to become ready and retry a few times
        for _ in range(5):
            try:
                from nodes.models import NetMessage

                NetMessage.broadcast(subject=f"{address}:{port}", body=body)
                break
            except Exception:
                time.sleep(1)

    threading.Thread(target=_worker, name="startup-notify", daemon=True).start()


def _trigger_startup_notification(**_: object) -> None:
    """Send the startup notification once a request has started."""

    request_started.disconnect(_trigger_startup_notification, dispatch_uid="nodes-startup")
    try:
        connections["default"].ensure_connection()
    except OperationalError:
        logger.exception("Startup notification skipped: database unavailable")
        return
    _startup_notification()


class NodesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "nodes"
    verbose_name = "2. Infrastructure"

    def ready(self):  # pragma: no cover - exercised on app start
        request_started.connect(
            _trigger_startup_notification, dispatch_uid="nodes-startup"
        )
