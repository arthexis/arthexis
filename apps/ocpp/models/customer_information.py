from __future__ import annotations

from .base import *


class CustomerInformationRequest(Entity):
    """Persisted record of CustomerInformation requests."""

    charger = models.ForeignKey(
        "Charger",
        on_delete=models.CASCADE,
        related_name="customer_information_requests",
    )
    ocpp_message_id = models.CharField(
        max_length=64,
        blank=True,
        verbose_name="OCPP message ID",
    )
    request_id = models.BigIntegerField(
        null=True,
        blank=True,
        verbose_name="Request ID",
    )
    payload = models.JSONField(default=dict, blank=True)
    last_notified_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = _("Customer Information Request")
        verbose_name_plural = _("Customer Information Requests")
        indexes = [
            models.Index(
                fields=["ocpp_message_id"],
                name="ocpp_custinfo_ocpp_idx",
            ),
            models.Index(
                fields=["charger", "request_id"],
                name="ocpp_custinfo_req_idx",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        request_label = self.request_id if self.request_id is not None else "unknown"
        return f"CustomerInformation {request_label}"


class CustomerInformationChunk(Entity):
    """Persisted chunk of CustomerInformation data."""

    charger = models.ForeignKey(
        "Charger",
        on_delete=models.CASCADE,
        related_name="customer_information_chunks",
    )
    request_record = models.ForeignKey(
        CustomerInformationRequest,
        on_delete=models.SET_NULL,
        related_name="chunks",
        null=True,
        blank=True,
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
    data = models.TextField(blank=True, default="")
    tbc = models.BooleanField(default=False, verbose_name="To Be Continued")
    raw_payload = models.JSONField(default=dict, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at", "-pk"]
        verbose_name = _("Customer Information Chunk")
        verbose_name_plural = _("Customer Information Chunks")
        indexes = [
            models.Index(
                fields=["ocpp_message_id"],
                name="ocpp_custinfochunk_ocpp_idx",
            ),
            models.Index(
                fields=["charger", "request_id"],
                name="ocpp_custinfochunk_req_idx",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        request_label = self.request_id if self.request_id is not None else "unknown"
        return f"CustomerInformation chunk {request_label}"
