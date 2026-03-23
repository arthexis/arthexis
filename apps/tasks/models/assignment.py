from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity


class ManualTaskReport(Entity):
    """Execution report submitted after completing a manual task request."""

    request = models.ForeignKey(
        "tasks.ManualTaskRequest",
        on_delete=models.CASCADE,
        related_name="reports",
        verbose_name=_("Task request"),
    )
    executor = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="executed_manual_task_reports",
        verbose_name=_("Executor"),
        help_text=_("User who performed the work. Can differ from the assignee."),
    )
    performed_at = models.DateTimeField(
        _("Performed at"),
        default=timezone.now,
        help_text=_("When the work occurred."),
    )
    duration = models.DurationField(
        _("Actual duration"),
        null=True,
        blank=True,
        help_text=_("Actual time spent completing the task."),
    )
    details = models.TextField(
        _("Details"),
        help_text=_("Executor notes and outcomes for this task."),
    )

    class Meta:
        verbose_name = _("Manual Task Report")
        verbose_name_plural = _("Manual Task Reports")
        ordering = ("-performed_at",)
        db_table = "core_manualtaskreport"

    def __str__(self):  # pragma: no cover - simple representation
        return _("Report for %(task)s") % {"task": self.request}

    def save(self, *args, **kwargs):
        """Persist report and trigger post-completion GitHub issue automation."""

        created = self.pk is None
        super().save(*args, **kwargs)
        if not created:
            return

        request = self.request
        if request.can_open_github_issue_for_trigger("completed"):
            from apps.celery.utils import enqueue_task
            from apps.tasks.tasks import create_manual_task_github_issue

            enqueue_task(create_manual_task_github_issue, request.pk, "completed")
