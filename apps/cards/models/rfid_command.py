from __future__ import annotations

import uuid
from typing import Any

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity

__all__ = ["RFIDCommandExecution"]


class RFIDCommandExecution(Entity):
    """Audit record for one RFID command-card execution attempt."""

    class Status(models.TextChoices):
        BLOCKED = "blocked", _("Blocked")
        STARTED = "started", _("Started")
        SUCCEEDED = "succeeded", _("Succeeded")
        FAILED = "failed", _("Failed")

    execution_id = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        help_text=_("Stable execution id persisted to command-card result blocks."),
    )
    rfid = models.ForeignKey(
        "cards.RFID",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="command_executions",
    )
    template = models.ForeignKey(
        "cards.RFIDCommandTemplate",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="command_executions",
    )
    attempt = models.ForeignKey(
        "cards.RFIDAttempt",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="command_executions",
    )
    rfid_value = models.CharField(max_length=255, db_index=True, blank=True)
    card_name = models.CharField(max_length=16, db_index=True, blank=True)
    card_provenance_key = models.CharField(max_length=16, db_index=True, blank=True)
    reader_id = models.CharField(max_length=64, blank=True)
    command_name = models.CharField(max_length=64, db_index=True, blank=True)
    command_params = models.JSONField(default=dict, blank=True)
    command_sigils = models.JSONField(default=dict, blank=True)
    command_payload = models.JSONField(default=dict, blank=True)
    run_as_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="rfid_command_executions",
        help_text=_("User whose permissions were used for this command."),
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.STARTED,
        db_index=True,
    )
    status_detail = models.TextField(blank=True)
    preflight_ok = models.BooleanField(default=False)
    card_result_before = models.JSONField(default=dict, blank=True)
    card_result_written = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)
    expected_previous_result_digest = models.CharField(max_length=64, blank=True)
    card_previous_result_digest = models.CharField(max_length=64, blank=True)
    result_digest = models.CharField(max_length=64, blank=True)
    triggered_at = models.DateTimeField(default=timezone.now, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-triggered_at", "-pk"]
        permissions = [
            ("run_suite_command_card", _("Can run RFID suite command cards")),
        ]
        verbose_name = _("RFID command execution")
        verbose_name_plural = _("RFID command executions")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        label = self.card_name or self.rfid_value or "-"
        return f"{label} {self.command_name or '-'} ({self.status})"

    def mark_blocked(
        self, detail: str, *, result: dict[str, Any] | None = None
    ) -> None:
        self.status = self.Status.BLOCKED
        self.status_detail = detail
        self.completed_at = timezone.now()
        if result is not None:
            self.result = result
        self.save(update_fields=["status", "status_detail", "completed_at", "result"])

    def mark_failed(self, detail: str, *, result: dict[str, Any] | None = None) -> None:
        self.status = self.Status.FAILED
        self.status_detail = detail
        self.completed_at = timezone.now()
        if result is not None:
            self.result = result
        self.save(update_fields=["status", "status_detail", "completed_at", "result"])

    def mark_succeeded(
        self,
        *,
        result: dict[str, Any],
        card_result_written: dict[str, Any],
        result_digest: str,
    ) -> None:
        self.status = self.Status.SUCCEEDED
        self.result = result
        self.card_result_written = card_result_written
        self.result_digest = result_digest
        self.completed_at = timezone.now()
        self.save(
            update_fields=[
                "status",
                "result",
                "card_result_written",
                "result_digest",
                "completed_at",
            ]
        )
