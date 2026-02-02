from __future__ import annotations

import importlib.util

from django.db.utils import OperationalError, ProgrammingError

from apps.celery.utils import (
    is_celery_enabled,
    normalize_periodic_task_name,
    periodic_task_name_variants,
)

from django.conf import settings

from .tasks import sample_thermometers, scan_usb_trackers

THERMOMETER_SAMPLING_TASK_NAME = "thermometer-sampling"
THERMOMETER_SAMPLING_TASK_PATH = sample_thermometers.name
USB_TRACKER_POLL_TASK_NAME = "usb-tracker-poll"
USB_TRACKER_POLL_TASK_PATH = scan_usb_trackers.name


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


def ensure_usb_tracker_poll_task(sender=None, **kwargs) -> None:
    """Ensure the USB tracker polling task is scheduled when enabled."""

    del sender, kwargs

    if importlib.util.find_spec("django_celery_beat") is None:
        return
    from django_celery_beat.models import IntervalSchedule, PeriodicTask

    task_names = periodic_task_name_variants(USB_TRACKER_POLL_TASK_NAME)

    if not is_celery_enabled():
        try:
            PeriodicTask.objects.filter(name__in=task_names).delete()
        except (OperationalError, ProgrammingError):
            return
        return

    poll_interval = int(getattr(settings, "USB_TRACKER_POLL_SECONDS", 10))
    if poll_interval <= 0:
        poll_interval = 10

    try:
        schedule, _ = IntervalSchedule.objects.get_or_create(
            every=poll_interval,
            period=IntervalSchedule.SECONDS,
        )
        task_name = normalize_periodic_task_name(
            PeriodicTask.objects, USB_TRACKER_POLL_TASK_NAME
        )
        PeriodicTask.objects.update_or_create(
            name=task_name,
            defaults={
                "interval": schedule,
                "task": USB_TRACKER_POLL_TASK_PATH,
            },
        )
    except (OperationalError, ProgrammingError):
        return


__all__ = ["ensure_thermometer_sampling_task", "ensure_usb_tracker_poll_task"]
