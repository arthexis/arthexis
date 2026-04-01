from __future__ import annotations

from .base import *


class ControlOperationEvent(Entity):
    """Audit trail for critical control operations initiated from admin tools."""

    class Transport(models.TextChoices):
        LOCAL = "local", _("Local websocket")
        REMOTE = "remote", _("Remote node")

    class Status(models.TextChoices):
        SENT = "sent", _("Sent")
        FAILED = "failed", _("Failed")

    charger = models.ForeignKey(
        "Charger",
        on_delete=models.CASCADE,
        related_name="control_operation_events",
    )
    transaction = models.ForeignKey(
        "Transaction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="control_operation_events",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ocpp_control_operation_events",
    )
    action = models.CharField(max_length=120)
    transport = models.CharField(max_length=16, choices=Transport.choices)
    status = models.CharField(max_length=16, choices=Status.choices)
    detail = models.CharField(max_length=255, blank=True, default="")
    request_payload = models.JSONField(default=dict, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = _("Control Operation Event")
        verbose_name_plural = _("Control Operation Events")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.charger}: {self.action} [{self.status}]"
