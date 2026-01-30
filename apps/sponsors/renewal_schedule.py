"""Celery beat schedule for sponsor renewals."""

from __future__ import annotations

from apps.celery.utils import (
    is_celery_enabled,
    normalize_periodic_task_name,
    periodic_task_name_variants,
)

from .tasks import process_sponsorship_renewals

SPONSOR_RENEWAL_TASK_NAME = "sponsor-renewals"
SPONSOR_RENEWAL_TASK_PATH = process_sponsorship_renewals.name


def ensure_sponsor_renewal_task(sender=None, **kwargs) -> None:
    """Ensure the sponsor renewal task is scheduled when enabled."""

    del sender, kwargs

    try:  # pragma: no cover - optional dependency
        from django_celery_beat.models import IntervalSchedule, PeriodicTask
        from django.db.utils import OperationalError, ProgrammingError
    except Exception:
        return

    task_names = periodic_task_name_variants(SPONSOR_RENEWAL_TASK_NAME)

    if not is_celery_enabled():
        try:
            PeriodicTask.objects.filter(name__in=task_names).delete()
        except (OperationalError, ProgrammingError):
            return
        return

    try:
        schedule, _ = IntervalSchedule.objects.get_or_create(
            every=1,
            period=IntervalSchedule.HOURS,
        )
        task_name = normalize_periodic_task_name(
            PeriodicTask.objects, SPONSOR_RENEWAL_TASK_NAME
        )
        PeriodicTask.objects.update_or_create(
            name=task_name,
            defaults={
                "interval": schedule,
                "task": SPONSOR_RENEWAL_TASK_PATH,
            },
        )
    except (OperationalError, ProgrammingError):
        return
