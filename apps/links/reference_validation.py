from __future__ import annotations

from apps.celery.utils import (
    is_celery_enabled,
    normalize_periodic_task_name,
    periodic_task_name_variants,
)
from apps.tasks.tasks import validate_reference_links


REFERENCE_VALIDATION_TASK_NAME = "reference-url-validation"
REFERENCE_VALIDATION_TASK_PATH = validate_reference_links.name


def ensure_reference_validation_task(sender=None, **kwargs) -> None:
    """Ensure the nightly reference validation task is scheduled when enabled."""

    del sender, kwargs

    try:  # pragma: no cover - optional dependency
        from django_celery_beat.models import CrontabSchedule, PeriodicTask
        from django.db.utils import OperationalError, ProgrammingError
    except Exception:
        return

    task_names = periodic_task_name_variants(REFERENCE_VALIDATION_TASK_NAME)

    if not is_celery_enabled():
        try:
            PeriodicTask.objects.filter(name__in=task_names).delete()
        except (OperationalError, ProgrammingError):
            return
        return

    try:
        schedule, _ = CrontabSchedule.objects.get_or_create(
            minute="0",
            hour="2",
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
        )
        task_name = normalize_periodic_task_name(
            PeriodicTask.objects, REFERENCE_VALIDATION_TASK_NAME
        )
        PeriodicTask.objects.update_or_create(
            name=task_name,
            defaults={
                "crontab": schedule,
                "task": REFERENCE_VALIDATION_TASK_PATH,
            },
        )
    except (OperationalError, ProgrammingError):
        return
