from __future__ import annotations

from .base import *


class DisplayMessageNotification(Entity):
    """Persisted record of NotifyDisplayMessages payloads."""

    charger = models.ForeignKey(
        "Charger",
        on_delete=models.CASCADE,
        related_name="display_message_notifications",
    )
    ocpp_message_id = models.CharField(
        max_length=64,
        verbose_name="OCPP message ID",
    )
    request_id = models.BigIntegerField(
        null=True,
        blank=True,
        verbose_name="Request ID",
    )
    tbc = models.BooleanField(default=False, verbose_name="To Be Continued")
    raw_payload = models.JSONField(default=dict, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-received_at", "-pk"]
        verbose_name = _("Display Message Notification")
        verbose_name_plural = _("Display Message Notifications")
        indexes = [
            models.Index(
                fields=["ocpp_message_id"],
                name="ocpp_display_ocpp_idx",
            ),
            models.Index(
                fields=["charger", "request_id"],
                name="ocpp_display_req_idx",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        request_label = self.request_id if self.request_id is not None else "unknown"
        return f"Display messages {request_label}"


class DisplayMessage(Entity):
    """Persisted display message contents."""

    notification = models.ForeignKey(
        DisplayMessageNotification,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    charger = models.ForeignKey(
        "Charger",
        on_delete=models.CASCADE,
        related_name="display_messages",
    )
    message_id = models.BigIntegerField(
        null=True,
        blank=True,
        verbose_name="Message ID",
    )
    priority = models.CharField(max_length=32, blank=True)
    state = models.CharField(max_length=32, blank=True)
    valid_from = models.DateTimeField(null=True, blank=True)
    valid_to = models.DateTimeField(null=True, blank=True)
    language = models.CharField(max_length=16, blank=True)
    content = models.TextField(blank=True)
    component_name = models.CharField(max_length=200, blank=True)
    component_instance = models.CharField(max_length=200, blank=True)
    variable_name = models.CharField(max_length=200, blank=True)
    variable_instance = models.CharField(max_length=200, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = _("Display Message")
        verbose_name_plural = _("Display Messages")
        indexes = [
            models.Index(
                fields=["message_id"],
                name="ocpp_display_msg_idx",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        message_label = self.message_id if self.message_id is not None else "message"
        return f"Display {message_label}"
