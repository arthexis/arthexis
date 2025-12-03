from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity


class AdminCommandResult(Entity):
    """Persisted output for ad-hoc Django management command runs."""

    command = models.TextField()
    resolved_command = models.TextField()
    command_name = models.CharField(max_length=150, blank=True)
    stdout = models.TextField(blank=True)
    stderr = models.TextField(blank=True)
    traceback = models.TextField(blank=True)
    runtime = models.DurationField(default=timedelta)
    exit_code = models.IntegerField(default=0)
    success = models.BooleanField(default=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="command_results",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = _("Command Result")
        verbose_name_plural = _("Command Results")

    def __str__(self) -> str:  # pragma: no cover - human-readable representation
        return self.command_name or self.command
