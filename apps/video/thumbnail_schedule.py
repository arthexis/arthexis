from __future__ import annotations

from apps.celery.utils import (
    is_celery_enabled,
    normalize_periodic_task_name,
    periodic_task_name_variants,
)

from .tasks import capture_mjpeg_thumbnails

MJPEG_THUMBNAIL_TASK_NAME = "mjpeg-stream-thumbnails"
MJPEG_THUMBNAIL_TASK_PATH = capture_mjpeg_thumbnails.name


def ensure_mjpeg_thumbnail_task(sender=None, **kwargs) -> None:
    """Ensure the MJPEG thumbnail capture task is scheduled when enabled."""

    del sender, kwargs

    try:  # pragma: no cover - optional dependency
        from django_celery_beat.models import IntervalSchedule, PeriodicTask
        from django.db.models import Min
        from django.db.utils import OperationalError, ProgrammingError
    except Exception:
        return

    task_names = periodic_task_name_variants(MJPEG_THUMBNAIL_TASK_NAME)

    if not is_celery_enabled():
        try:
            PeriodicTask.objects.filter(name__in=task_names).delete()
        except (OperationalError, ProgrammingError):
            return
        return

    try:
        from .models import MjpegStream

        min_frequency = (
            MjpegStream.objects.filter(thumbnail_frequency__gt=0)
            .aggregate(Min("thumbnail_frequency"))
            .get("thumbnail_frequency__min")
            or 60
        )
        schedule, _ = IntervalSchedule.objects.get_or_create(
            every=max(1, int(min_frequency)),
            period=IntervalSchedule.SECONDS,
        )
        task_name = normalize_periodic_task_name(
            PeriodicTask.objects, MJPEG_THUMBNAIL_TASK_NAME
        )
        PeriodicTask.objects.update_or_create(
            name=task_name,
            defaults={
                "interval": schedule,
                "task": MJPEG_THUMBNAIL_TASK_PATH,
            },
        )
    except (OperationalError, ProgrammingError):
        return
