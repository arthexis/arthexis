"""Runtime controls for Celery worker count exposed as a suite feature."""

from __future__ import annotations

from pathlib import Path
import logging
import subprocess

from django.conf import settings
from django.core.cache import cache
from django.db.utils import OperationalError, ProgrammingError

from apps.core.systemctl import _systemctl_command
from apps.features.parameters import get_feature_parameter

from .lifecycle import lock_dir, read_service_name


logger = logging.getLogger(__name__)

CELERY_WORKERS_FEATURE_SLUG = "celery-workers"
CELERY_WORKERS_PARAM_KEY = "worker_count"
CELERY_WORKERS_LOCK_NAME = "celery_workers.lck"


def parse_worker_count(raw_value: object, *, default: int = 1) -> int:
    """Return a normalized positive worker count from ``raw_value``."""

    try:
        value = int(str(raw_value).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def configured_worker_count(*, default: int = 1) -> int:
    """Return the configured Celery worker count from suite feature parameters."""

    try:
        raw_value = get_feature_parameter(
            CELERY_WORKERS_FEATURE_SLUG,
            CELERY_WORKERS_PARAM_KEY,
            fallback=str(default),
        )
    except (OperationalError, ProgrammingError):
        raw_value = str(default)
    return parse_worker_count(raw_value, default=default)


def celery_workers_lock_path(base_dir: Path | None = None) -> Path:
    """Return the lock file path used to share worker count with runtime services."""

    return lock_dir(base_dir or Path(settings.BASE_DIR)) / CELERY_WORKERS_LOCK_NAME


def persist_worker_count(worker_count: int, *, base_dir: Path | None = None) -> Path:
    """Persist ``worker_count`` to the Celery worker count lock file."""

    lock_path = celery_workers_lock_path(base_dir)
    lock_path.write_text(f"{worker_count}\n", encoding="utf-8")
    return lock_path


def restart_celery_service(*, base_dir: Path | None = None) -> bool:
    """Restart the local celery worker service when systemctl and service name exist."""

    command = _systemctl_command()
    if not command:
        return False

    locks = lock_dir(base_dir or Path(settings.BASE_DIR))
    service_name = read_service_name(locks / "service.lck")
    if not service_name:
        return False

    unit_name = f"celery-{service_name}.service"
    try:
        result = subprocess.run([*command, "restart", unit_name], check=False, capture_output=True, text=True)
        if result.returncode != 0:
            logger.warning(
                "Failed to restart celery service %s (exit code %d). stderr: %s",
                unit_name,
                result.returncode,
                result.stderr.strip(),
            )
            return False
    except OSError:
        logger.warning("Unable to restart celery service %s", unit_name, exc_info=True)
        return False
    return True


def sync_celery_workers_from_feature(*, base_dir: Path | None = None) -> tuple[int, bool]:
    """Persist worker count from suite feature parameters and restart Celery service."""

    cache.delete(f"feature-param:{CELERY_WORKERS_FEATURE_SLUG}:{CELERY_WORKERS_PARAM_KEY}")
    worker_count = configured_worker_count()
    persist_worker_count(worker_count, base_dir=base_dir)
    restarted = restart_celery_service(base_dir=base_dir)
    return worker_count, restarted
