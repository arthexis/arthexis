from __future__ import annotations

from .base import *
from .data_transfer_message import DataTransferMessage

class CPFirmwareRequest(Entity):
    """Temporary record tracking CP firmware requests."""

    charger = models.ForeignKey(
        "Charger",
        on_delete=models.CASCADE,
        related_name="firmware_requests",
    )
    connector_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Connector ID",
    )
    vendor_id = models.CharField(max_length=255, blank=True)
    message = models.OneToOneField(
        DataTransferMessage,
        on_delete=models.CASCADE,
        related_name="firmware_request",
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=64, blank=True)
    response_payload = models.JSONField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-requested_at"]
        verbose_name = _("CP Firmware Request")
        verbose_name_plural = _("CP Firmware Requests")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"Firmware request for {self.charger}" if self.pk else "Firmware request"
