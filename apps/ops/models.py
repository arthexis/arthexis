"""Database models for reusable operations screens and execution logs."""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class OperationScreen(models.Model):
    """A guided, ownable operation that users can execute on a cadence."""

    class Scope(models.TextChoices):
        USER = "user", "Once per user"
        NODE = "node", "Once per node"

    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True)
    description = models.TextField()
    start_url = models.URLField(help_text="Internal URL where the operation starts.")
    reference_url = models.URLField(blank=True)
    sql_validation = models.TextField(blank=True)
    priority = models.PositiveSmallIntegerField(default=100)
    is_required = models.BooleanField(default=False)
    scope = models.CharField(max_length=16, choices=Scope.choices, default=Scope.USER)
    expires_after_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="If set, completion expires after this many days.",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_operation_screens",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("priority", "title")

    def __str__(self) -> str:
        """Return the operation title for admin lists."""

        return self.title

    def clean(self) -> None:
        """Validate expiration schedule values."""

        super().clean()
        if self.expires_after_days is not None and self.expires_after_days < 1:
            raise ValidationError({"expires_after_days": "Expiration must be at least 1 day."})

    def next_expiration_for(self, completed_at):
        """Return calculated expiration date for a completion timestamp."""

        if not self.expires_after_days or not completed_at:
            return None
        return completed_at + timedelta(days=self.expires_after_days)


class OperationExecution(models.Model):
    """A completion log entry for an operation by user and optional node."""

    operation = models.ForeignKey(
        OperationScreen,
        on_delete=models.CASCADE,
        related_name="executions",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="operation_executions",
    )
    node = models.ForeignKey(
        "nodes.Node",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="operation_executions",
    )
    notes = models.TextField(blank=True)
    completed_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-completed_at",)

    def __str__(self) -> str:
        """Return a concise execution summary."""

        return f"{self.operation} by {self.user}"


class OperationReminder(models.Model):
    """Track reminders sent when operation completions expire."""

    execution = models.ForeignKey(
        OperationExecution,
        on_delete=models.CASCADE,
        related_name="reminders",
    )
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("execution",)
