"""Startup-oriented OCPP maintenance tasks."""

from celery import shared_task

from apps.ocpp.maintenance import reset_cached_statuses


@shared_task(name="apps.ocpp.tasks.reset_cached_statuses")
def reset_cached_statuses_task() -> int:
    """Reset persisted cached charger statuses when startup maintenance runs."""

    return reset_cached_statuses()
