from __future__ import annotations

import importlib.util

from django.db.utils import OperationalError, ProgrammingError

from apps.celery.utils import (
    is_celery_enabled,
    normalize_periodic_task_name,
    periodic_task_name_variants,
)

from .tasks import sample_thermometers

THERMOMETER_SAMPLING_TASK_NAME = "thermometer-sampling"
THERMOMETER_SAMPLING_TASK_PATH = sample_thermometers.name


def ensure_thermometer_sampling_task(sender=None, **kwargs) -> None:
    """Ensure the thermometer sampling task is scheduled when enabled."""

    del sender, kwargs

    if importlib.util.find_spec("django_celery_beat") is None:
        return
    from django_celery_beat.models import IntervalSchedule, PeriodicTask

    task_names = periodic_task_name_variants(THERMOMETER_SAMPLING_TASK_NAME)

    if not is_celery_enabled():
        try:
            PeriodicTask.objects.filter(name__in=task_names).delete()
        except (OperationalError, ProgrammingError):
            return
        return

    try:
        schedule, _ = IntervalSchedule.objects.get_or_create(
            every=1,
            period=IntervalSchedule.SECONDS,
        )
        task_name = normalize_periodic_task_name(
            PeriodicTask.objects, THERMOMETER_SAMPLING_TASK_NAME
        )
        PeriodicTask.objects.update_or_create(
            name=task_name,
            defaults={
                "interval": schedule,
                "task": THERMOMETER_SAMPLING_TASK_PATH,
            },
        )
    except (OperationalError, ProgrammingError):
        return


__all__ = ["ensure_thermometer_sampling_task"]
