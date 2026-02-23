"""Celery tasks for operations lifecycle notifications."""

from __future__ import annotations

from datetime import timedelta
import logging

from celery import shared_task
from django.db.models import Max
from django.utils import timezone

from .models import OperationExecution, OperationScreen


logger = logging.getLogger(__name__)


@shared_task(name="apps.ops.tasks.notify_expired_operations")
def notify_expired_operations() -> int:
    """Notify users for recurring operations that have expired after a prior completion."""

    now = timezone.now()
    notified = 0

    operations = OperationScreen.objects.filter(is_active=True, recurrence_days__isnull=False)
    for operation in operations:
        threshold = now - timedelta(days=operation.recurrence_days or 0)
        latest_per_user = (
            OperationExecution.objects.filter(operation=operation)
            .values("user_id")
            .annotate(latest_performed_at=Max("performed_at"))
        )

        for row in latest_per_user:
            execution = (
                OperationExecution.objects.filter(
                    operation=operation,
                    user_id=row["user_id"],
                    performed_at=row["latest_performed_at"],
                )
                .select_related("user")
                .order_by("-id")
                .first()
            )
            if execution is None or execution.performed_at > threshold:
                continue
            if execution.expiration_notified_at and execution.expiration_notified_at >= threshold:
                continue
            if not execution.user.email:
                continue
            try:
                execution.user.email_user(
                    subject=f"Operation expired: {operation.title}",
                    message=(
                        "A recurring operation you previously completed has expired and must be "
                        f"performed again: {operation.title}."
                    ),
                )
            except Exception:
                logger.exception(
                    "Failed to send expiration email for operation %s to user %s",
                    operation.pk,
                    execution.user_id,
                )
                continue
            OperationExecution.objects.filter(pk=execution.pk).update(
                expiration_notified_at=now,
            )
            notified += 1

    return notified
