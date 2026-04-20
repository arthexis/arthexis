"""Periodic task wiring for the experimental camera classification loop."""

from __future__ import annotations

from django.conf import settings

from apps.celery.utils import (
    is_celery_enabled,
    normalize_periodic_task_name,
    periodic_task_name_variants,
)

from .tasks import classify_camera_streams


CAMERA_CLASSIFICATION_TASK_NAME = "camera-classification-loop"
CAMERA_CLASSIFICATION_TASK_PATH = classify_camera_streams.name


def ensure_camera_classification_task(sender=None, **kwargs) -> None:
    """Ensure the camera classification loop task matches current settings."""

    del sender, kwargs

    try:  # pragma: no cover - optional dependency at runtime
        from django_celery_beat.models import IntervalSchedule, PeriodicTask
        from django.db.utils import OperationalError, ProgrammingError
    except Exception:
        return

    task_names = periodic_task_name_variants(CAMERA_CLASSIFICATION_TASK_NAME)
    enabled = bool(getattr(settings, "CLASSIFICATION_CAMERA_LOOP_ENABLED", False))
    if not is_celery_enabled() or not enabled:
        try:
            PeriodicTask.objects.filter(name__in=task_names).delete()
        except (OperationalError, ProgrammingError):
            return
        return

    interval_seconds = int(getattr(settings, "CLASSIFICATION_CAMERA_LOOP_INTERVAL_SECONDS", 60) or 60)
    try:
        schedule, _ = IntervalSchedule.objects.get_or_create(
            every=max(interval_seconds, 1),
            period=IntervalSchedule.SECONDS,
        )
        task_name = normalize_periodic_task_name(
            PeriodicTask.objects, CAMERA_CLASSIFICATION_TASK_NAME
        )
        PeriodicTask.objects.update_or_create(
            name=task_name,
            defaults={
                "interval": schedule,
                "task": CAMERA_CLASSIFICATION_TASK_PATH,
            },
        )
    except (OperationalError, ProgrammingError):
        return

