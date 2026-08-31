from __future__ import annotations

from .base import *

class DataTransferMessage(Entity):
    """Persisted record of OCPP DataTransfer exchanges."""

    DIRECTION_CP_TO_CSMS = "cp_to_csms"
    DIRECTION_CSMS_TO_CP = "csms_to_cp"
    DIRECTION_CHOICES = (
        (DIRECTION_CP_TO_CSMS, _("Charge Point → CSMS")),
        (DIRECTION_CSMS_TO_CP, _("CSMS → Charge Point")),
    )

    charger = models.ForeignKey(
        "Charger",
        on_delete=models.CASCADE,
        related_name="data_transfer_messages",
    )
    connector_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Connector ID",
    )
    direction = models.CharField(max_length=16, choices=DIRECTION_CHOICES)
    ocpp_message_id = models.CharField(
        max_length=64,
        verbose_name="OCPP message ID",
    )
    vendor_id = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Vendor ID",
    )
    message_id = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Message ID",
    )
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=64, blank=True)
    response_data = models.JSONField(null=True, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    error_description = models.TextField(blank=True)
    error_details = models.JSONField(null=True, blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("CP Data Message")
        verbose_name_plural = _("CP Data Messages")
        indexes = [
            models.Index(
                fields=["ocpp_message_id"],
                name="ocpp_datatr_ocpp_me_70d17f_idx",
            ),
            models.Index(
                fields=["vendor_id"], name="ocpp_datatr_vendor__59e1c7_idx"
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.get_direction_display()} {self.vendor_id or 'DataTransfer'}"
