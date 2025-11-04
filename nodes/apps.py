import logging
import os
import socket
import sys
import threading
import time
from pathlib import Path

from django.apps import AppConfig
from django.conf import settings
from django.db import connections
from django.db.utils import OperationalError
from utils import revision


logger = logging.getLogger(__name__)


def _startup_notification() -> None:
    """Queue a Net Message with ``hostname:port`` and version info."""

    host = socket.gethostname()

    port = os.environ.get("PORT", "8888")

    version = ""
    ver_path = Path(settings.BASE_DIR) / "VERSION"
    if ver_path.exists():
        version = ver_path.read_text().strip()

    revision_value = revision.get_revision()
    rev_short = revision_value[-6:] if revision_value else ""

    body = version
    if body:
        normalized = body.lstrip("vV") or body
        base_version = normalized.rstrip("+")
        needs_marker = False
        if base_version and revision_value:
            try:  # pragma: no cover - defensive guard
                from core.models import PackageRelease

                needs_marker = not PackageRelease.matches_revision(
                    base_version, revision_value
                )
            except Exception:
                logger.debug("Startup release comparison failed", exc_info=True)
        if needs_marker and not normalized.endswith("+"):
            body = f"{body}+"
    if rev_short:
        body = f"{body} r{rev_short}" if body else f"r{rev_short}"

    def _worker() -> None:  # pragma: no cover - background thread
        # Allow the LCD a moment to become ready and retry a few times
        for _ in range(5):
            try:
                from nodes.models import NetMessage

                NetMessage.broadcast(subject=f"{host}:{port}", body=body)
                break
            except Exception:
                time.sleep(1)

    threading.Thread(target=_worker, name="startup-notify", daemon=True).start()


def _trigger_startup_notification(**_: object) -> None:
    """Attempt to send the startup notification in the background."""

    if _is_running_migration_command():
        logger.debug("Startup notification skipped: running migration command")
        return

    try:
        connections["default"].ensure_connection()
    except OperationalError:
        logger.exception("Startup notification skipped: database unavailable")
        return
    _startup_notification()


def _is_running_migration_command() -> bool:
    """Return ``True`` when Django's ``migrate`` command is executing."""

    return len(sys.argv) > 1 and sys.argv[1] == "migrate"


class NodesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "nodes"
    verbose_name = "4. Infrastructure"

    def ready(self):  # pragma: no cover - exercised on app start
        from django.db.models.signals import post_migrate

        post_migrate.connect(_trigger_startup_notification, sender=self)
        # Import signal handlers for content classifiers
        from . import signals  # noqa: F401
