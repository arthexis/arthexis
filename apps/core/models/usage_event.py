from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone


class UsageEvent(models.Model):
    class Action(models.TextChoices):
        READ = "read", "Read"
        CREATE = "create", "Create"
        UPDATE = "update", "Update"
        DELETE = "delete", "Delete"

    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="usage_events",
    )
    app_label = models.CharField(max_length=100, db_index=True)
    view_name = models.CharField(max_length=255, db_index=True)
    path = models.TextField()
    method = models.CharField(max_length=10)
    status_code = models.PositiveIntegerField()
    model_label = models.CharField(max_length=255, blank=True, default="")
    action = models.CharField(
        max_length=12,
        choices=Action.choices,
        default=Action.READ,
        db_index=True,
    )
    metadata = models.JSONField(blank=True, default=dict)

    class Meta:
        indexes = [
            models.Index(fields=["app_label", "view_name", "timestamp"]),
        ]
        ordering = ["-timestamp"]

    def __str__(self) -> str:  # pragma: no cover - human-readable fallback
        return f"{self.app_label}:{self.view_name} ({self.action})"
