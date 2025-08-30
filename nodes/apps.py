import os
import socket
import threading
import time
from pathlib import Path

from django.apps import AppConfig
from django.conf import settings
from django.db.models.signals import post_migrate
from utils import revision


def _startup_notification() -> None:
    """Queue a notification with host:port and version on a background thread."""

    try:  # import here to avoid circular import during app loading
        from core.notifications import notify
    except Exception:  # pragma: no cover - failure shouldn't break startup
        return

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
            if notify(f"{address}:{port}", body):
                break
            time.sleep(1)

    threading.Thread(target=_worker, name="startup-notify", daemon=True).start()


def _ensure_release(**kwargs) -> None:
    """Create a package release for the current version if missing."""

    try:  # import lazily to avoid app loading issues
        from core.models import Package, PackageRelease

        ver_path = Path(settings.BASE_DIR) / "VERSION"
        if not ver_path.exists():  # pragma: no cover - no version file
            return
        version = ver_path.read_text().strip()

        package, _ = Package.objects.get_or_create(name="arthexis")
        PackageRelease.objects.get_or_create(package=package, version=version)
    except Exception:  # pragma: no cover - best effort only
        return


class NodesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "nodes"
    verbose_name = "Node Infrastructure"

    def ready(self):  # pragma: no cover - exercised on app start
        _startup_notification()
        post_migrate.connect(
            _ensure_release, dispatch_uid="nodes.ensure_release"
        )
