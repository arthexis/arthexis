from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable, Mapping, MutableMapping
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.db import transaction
from django.db.utils import IntegrityError
from kombu.exceptions import OperationalError

from utils.env import env_bool

logger = logging.getLogger(__name__)


def celery_lock_path(base_dir: Path | str | None = None) -> Path:
    """Return the path of the Celery feature lock file."""

    resolved_base_dir = Path(
        base_dir or getattr(settings, "BASE_DIR", Path(__file__).resolve().parents[2])
    )
    return resolved_base_dir / ".locks" / "celery.lck"


def is_celery_enabled(lock_path: Path | str | None = None) -> bool:
    """Return ``True`` when the Celery feature lock file exists, unless disabled."""

    if env_bool("ARTHEXIS_DISABLE_CELERY", False):
        return False

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


def resolve_crontab_timezone(timezone_name: str | None = None) -> str:
    """Return a validated timezone name for managed django-celery-beat crontabs."""

    candidates = (
        timezone_name,
        getattr(settings, "CELERY_TIMEZONE", None),
        getattr(settings, "TIME_ZONE", None),
        "UTC",
    )
    for candidate in candidates:
        if not candidate:
            continue
        resolved = str(candidate)
        try:
            ZoneInfo(resolved)
        except ZoneInfoNotFoundError:
            continue
        return resolved
    return "UTC"


def slugify_task_name(name: str) -> str:
    """Return a slugified task name using dashes."""

    slug = re.sub(r"[._]+", "-", name)
    slug = re.sub(r"-{2,}", "-", slug)
    return slug


def periodic_task_name_variants(name: str) -> set[str]:
    """Return legacy and slugified variants for a periodic task name."""

    slug = slugify_task_name(name)
    if slug == name:
        return {name}
    return {name, slug}


def _schedule_has_unmanaged_tasks(schedule, managed_task_names: set[str]) -> bool:
    from django_celery_beat.models import PeriodicTask

    tasks = PeriodicTask.objects.filter(crontab=schedule)
    if managed_task_names:
        tasks = tasks.exclude(name__in=managed_task_names)
    return tasks.exists()


def get_or_create_crontab_schedule(
    manager,
    *,
    minute: str,
    hour: str,
    day_of_week: str,
    day_of_month: str,
    month_of_year: str,
    timezone_name: str | None = None,
    managed_task_names: Iterable[str] | None = None,
):
    """Return one managed crontab schedule and fold legacy timezone duplicates.

    django-celery-beat derives the default crontab timezone from Celery's current
    app namespace. Plain Django startup and Celery-initialized processes can
    therefore create the same cron tuple with different timezone values. Arthexis
    managed schedules use the explicit Celery timezone and consolidate legacy
    duplicates so future lookups stay unambiguous.
    """

    managed_names = {
        str(name)
        for name in managed_task_names or ()
        if name is not None and str(name).strip()
    }
    cron_fields = {
        "minute": minute,
        "hour": hour,
        "day_of_week": day_of_week,
        "day_of_month": day_of_month,
        "month_of_year": month_of_year,
    }
    schedule_timezone = resolve_crontab_timezone(timezone_name)
    lookup = {**cron_fields, "timezone": schedule_timezone}

    def create_or_refetch():
        try:
            with transaction.atomic():
                return manager.create(**lookup), True
        except IntegrityError:
            return (
                manager.select_for_update(of=("self",)).get(**lookup),
                False,
            )

    with transaction.atomic():
        candidates = list(
            manager.select_for_update(of=("self",))
            .filter(**cron_fields)
            .order_by("pk")
        )
        created = False
        if candidates:
            schedule = next(
                (
                    candidate
                    for candidate in candidates
                    if str(candidate.timezone) == schedule_timezone
                ),
                None,
            )
            if schedule is None:
                schedule = next(
                    (
                        candidate
                        for candidate in candidates
                        if not _schedule_has_unmanaged_tasks(candidate, managed_names)
                    ),
                    None,
                )
            if schedule is None:
                schedule, created = create_or_refetch()
                if created:
                    candidates.append(schedule)
        else:
            schedule, created = create_or_refetch()
            if created:
                return schedule, True
            candidates = [schedule]

        changed = False
        if str(schedule.timezone) != schedule_timezone:
            schedule.timezone = schedule_timezone
            schedule.save(update_fields=["timezone"])
            changed = True

        duplicate_ids = [candidate.pk for candidate in candidates if candidate.pk != schedule.pk]
        if duplicate_ids:
            from django_celery_beat.models import PeriodicTask, PeriodicTasks

            if managed_names:
                changed = (
                    PeriodicTask.objects.filter(
                        crontab_id__in=duplicate_ids,
                        name__in=managed_names,
                    ).update(crontab=schedule)
                    > 0
                ) or changed

            deleted_duplicates = False
            for duplicate in manager.filter(pk__in=duplicate_ids).order_by("pk"):
                if PeriodicTask.objects.filter(crontab=duplicate).exists():
                    continue
                duplicate.delete()
                deleted_duplicates = True
            changed = changed or deleted_duplicates

        if changed:
            from django_celery_beat.models import PeriodicTasks

            PeriodicTasks.update_changed()

        return schedule, created


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
    except OperationalError as exc:
        logger.warning(
            "Celery broker unavailable; skipped enqueue for task %s: %s",
            _task_label(task),
            exc,
        )
        return False
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
    except OperationalError as exc:
        logger.warning(
            "Celery broker unavailable; skipped schedule for task %s: %s",
            _task_label(task),
            exc,
        )
        return False
    except Exception:  # pragma: no cover - defensive logging
        logger.exception("Failed to enqueue task %s", _task_label(task))
        return False
    return True


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
