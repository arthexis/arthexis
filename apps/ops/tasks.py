"""Celery tasks for operations lifecycle notifications."""

from __future__ import annotations

from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from .models import OperationExecution, OperationScreen


@shared_task(name="apps.ops.tasks.notify_expired_operations")
def notify_expired_operations() -> int:
    """Notify users for recurring operations that have expired after a prior completion."""

    now = timezone.now()
    notified = 0

    operations = OperationScreen.objects.filter(is_active=True, recurrence_days__isnull=False)
    for operation in operations:
        threshold = now - timedelta(days=operation.recurrence_days or 0)
        executions = (
            OperationExecution.objects.filter(operation=operation, performed_at__lte=threshold)
            .select_related("user")
            .order_by("user_id", "-performed_at")
        )
        seen_users: set[int] = set()
        for execution in executions:
            if execution.user_id in seen_users:
                continue
            seen_users.add(execution.user_id)
            if execution.expiration_notified_at and execution.expiration_notified_at >= threshold:
                continue
            if not execution.user.email:
                continue
            execution.user.email_user(
                subject=f"Operation expired: {operation.title}",
                message=(
                    "A recurring operation you previously completed has expired and must be "
                    f"performed again: {operation.title}."
                ),
            )
            execution.expiration_notified_at = now
            execution.save(update_fields=["expiration_notified_at"])
            notified += 1

    return notified
