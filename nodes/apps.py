import os
import socket
from pathlib import Path

from django.apps import AppConfig
from django.conf import settings


def _startup_notification() -> None:
    """Send an initial notification with host:port and version.

    This function is called when the :mod:`nodes` application is ready.
    It queues a notification that will be displayed on an attached LCD
    screen (or desktop notification if the screen is unavailable).
    """

    try:  # import here to avoid circular import during app loading
        from .notifications import notify
    except Exception:  # pragma: no cover - failure shouldn't break startup
        return

    host = socket.gethostname()
    # Prefer IP address over hostname when available
    try:
        address = socket.gethostbyname(host)
    except socket.gaierror:
        address = host

    port = os.environ.get("PORT", "8000")

    version = ""
    ver_path = Path(settings.BASE_DIR) / "VERSION"
    if ver_path.exists():
        version = ver_path.read_text().strip()

    revision = ""
    rev_path = Path(settings.BASE_DIR) / "REVISION"
    if rev_path.exists():
        revision = rev_path.read_text().strip()[-4:]

    line2 = f"v{version}"
    if revision:
        line2 += f" r{revision}"

    notify(f"{address}:{port}", line2)


class NodesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "nodes"
    verbose_name = "Node Infrastructure"

    def ready(self):  # pragma: no cover - exercised on app start
        _startup_notification()
