from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Mapping, MutableMapping, Set

from django.conf import settings
from django.db import transaction
from django.db.utils import IntegrityError

logger = logging.getLogger(__name__)


def celery_lock_path(base_dir: Path | str | None = None) -> Path:
    """Return the path of the Celery feature lock file."""

    resolved_base_dir = Path(
        base_dir or getattr(settings, "BASE_DIR", Path(__file__).resolve().parents[2])
    )
    return resolved_base_dir / ".locks" / "celery.lck"


def is_celery_enabled(lock_path: Path | str | None = None) -> bool:
    """Return ``True`` when the Celery feature lock file exists."""

    path = Path(lock_path) if lock_path is not None else celery_lock_path()
    return path.exists()


def celery_feature_enabled(node=None, lock_path: Path | str | None = None) -> bool:
    """Return ``True`` when Celery support is enabled for the given node."""

    if node is not None and hasattr(node, "has_feature"):
        try:
            if node.has_feature("celery-queue"):
                return True
        except Exception:  # pragma: no cover - defensive guard
            pass

    return is_celery_enabled(lock_path)


def resolve_celery_shutdown_timeout(
    env: Mapping[str, str] | MutableMapping[str, str] | None = None,
    default: float = 60.0,
) -> float:
    """Return the configured Celery soft shutdown timeout in seconds."""

    if env is None:
        env = os.environ

    candidates = (
        "CELERY_WORKER_SOFT_SHUTDOWN_TIMEOUT",
        "CELERY_WORKER_SHUTDOWN_TIMEOUT",
    )
    for variable in candidates:
        raw_value = (env.get(variable) or "").strip()
        if not raw_value:
            continue
        try:
            parsed = float(raw_value)
        except (TypeError, ValueError):
            continue
        if parsed < 0:
            continue
        return parsed

    return float(default)


def _task_label(task) -> str:
    name = getattr(task, "name", None)
    if name:
        return str(name)
    return getattr(task, "__name__", str(task))


def enqueue_task(task, *args, require_enabled: bool = True, **kwargs) -> bool:
    """Queue a Celery task and return ``True`` when it is enqueued."""

    if require_enabled and not is_celery_enabled():
        return False

    try:
        task.delay(*args, **kwargs)
    except Exception:  # pragma: no cover - defensive logging
        logger.exception("Failed to enqueue task %s", _task_label(task))
        return False
    return True


def schedule_task(
    task,
    *,
    args: tuple | list | None = None,
    kwargs: dict | None = None,
    require_enabled: bool = True,
    **options,
) -> bool:
    """Queue a Celery task via ``apply_async`` and return ``True`` when enqueued."""

    if require_enabled and not is_celery_enabled():
        return False

    try:
        task.apply_async(args=args or (), kwargs=kwargs or {}, **options)
    except Exception:  # pragma: no cover - defensive logging
        logger.exception("Failed to enqueue task %s", _task_label(task))
        return False
    return True


def slugify_task_name(name: str) -> str:
    """Return a slugified task name using dashes."""

    slug = re.sub(r"[._]+", "-", name)
    slug = re.sub(r"-{2,}", "-", slug)
    return slug


def periodic_task_name_variants(name: str) -> Set[str]:
    """Return legacy and slugified variants for a periodic task name."""

    slug = slugify_task_name(name)
    if slug == name:
        return {name}
    return {name, slug}


def _reassign_client_report_schedule(source, target) -> None:
    """Move the client report FK to the surviving periodic task if needed."""

    related_attr = getattr(source, "client_report_schedule", None)
    if related_attr and getattr(target, "client_report_schedule", None) is None:
        related_attr.periodic_task = target
        related_attr.save(update_fields=["periodic_task"])


def normalize_periodic_task_name(manager, name: str) -> str:
    """Ensure the stored periodic task name matches the slugified form."""

    slug = slugify_task_name(name)
    variants = periodic_task_name_variants(name)

    if variants == {slug}:
        return slug

    tasks = list(manager.filter(name__in=variants))
    if not tasks:
        return slug

    canonical = next((task for task in tasks if task.name == slug), tasks[0])

    for task in tasks:
        if task.pk == canonical.pk:
            continue
        _reassign_client_report_schedule(task, canonical)
        task.delete()

    if canonical.name == slug:
        return slug

    canonical.name = slug
    try:
        with transaction.atomic():
            canonical._core_normalizing = True
            canonical.save(update_fields=["name"])
    except IntegrityError:
        canonical.refresh_from_db()
        if canonical.name != slug:
            conflict = manager.filter(name=slug).exclude(pk=canonical.pk).first()
            if conflict:
                _reassign_client_report_schedule(canonical, conflict)
                canonical.delete()
                canonical = conflict
    finally:
        if hasattr(canonical, "_core_normalizing"):
            del canonical._core_normalizing

    return canonical.name
