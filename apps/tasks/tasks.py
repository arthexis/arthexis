from __future__ import annotations

import logging

from celery import shared_task


logger = logging.getLogger(__name__)


@shared_task
def send_manual_task_notification(manual_task_id: int, trigger: str) -> None:
    """Send reminder emails for the given manual task."""

    from apps.tasks.models import ManualTask

    task = ManualTask.objects.filter(pk=manual_task_id).first()
    if task is None:
        logger.debug(
            "ManualTask notification skipped; task %s not found", manual_task_id
        )
        return
    if not task.enable_notifications:
        logger.debug(
            "ManualTask notification skipped; notifications disabled for %s",
            manual_task_id,
        )
        return
    try:
        sent = task.send_notification_email(trigger)
    except Exception:  # pragma: no cover - defensive logging
        logger.exception(
            "ManualTask notification failed for %s using trigger %s",
            manual_task_id,
            trigger,
        )
        return
    if not sent:
        logger.debug(
            "ManualTask notification skipped; no recipients for %s",
            manual_task_id,
        )
