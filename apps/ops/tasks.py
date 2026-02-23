"""Celery tasks for operation expiration reminders."""

from __future__ import annotations

from django.db.models import Exists, OuterRef
from django.utils import timezone
from celery import shared_task

from apps.emails import mailer

from .models import OperationExecution, OperationReminder


@shared_task(name="apps.ops.tasks.notify_expired_operations")
def notify_expired_operations() -> int:
    """Notify users about expired operations that were previously completed."""

    reminders = OperationReminder.objects.filter(execution=OuterRef("pk"))
    executions = (
        OperationExecution.objects.select_related("operation", "user")
        .annotate(already_notified=Exists(reminders))
        .filter(already_notified=False, operation__expires_after_days__isnull=False)
    )

    now = timezone.now()
    sent = 0
    for execution in executions:
        expires_at = execution.operation.next_expiration_for(execution.completed_at)
        if not expires_at or expires_at > now:
            continue
        if not execution.user.email:
            continue
        mailer.send(
            subject=f"Operation expired: {execution.operation.title}",
            message=(
                "An operation you completed has expired and should be performed again.\n\n"
                f"Operation: {execution.operation.title}\n"
                f"Start: {execution.operation.start_url}\n"
            ),
            recipient_list=[execution.user.email],
            fail_silently=True,
        )
        OperationReminder.objects.get_or_create(execution=execution)
        sent += 1
    return sent
