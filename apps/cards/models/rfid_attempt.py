from __future__ import annotations

from typing import Any

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity


class RFIDAttempt(Entity):
    """Persist RFID scans and authentication attempts across services."""

    class Source(models.TextChoices):
        SERVICE = "service", _("Scanner service")
        BROWSER = "browser", _("Browser submission")
        CAMERA = "camera", _("Camera scan")
        ON_DEMAND = "on-demand", _("On-demand scan")
        OCPP = "ocpp", _("OCPP")
        AUTH = "auth", _("Authentication")

    class Reason(models.TextChoices):
        """Stable reason codes used in payload metadata for audit analytics."""

        RFID_MISSING = "rfid_missing", _("RFID value missing")
        TAG_NOT_FOUND = "tag_not_found", _("RFID tag not found")
        TAG_NOT_ALLOWED = "tag_not_allowed", _("RFID tag not allowed")
        USER_NOT_LINKED = "user_not_linked", _("User not linked")
        READ_ERROR = "read_error", _("RFID read error")
        RFID_MISMATCH = "rfid_mismatch", _("RFID mismatch")
        CELL_VALUE_MISMATCH = "cell_value_mismatch", _("Cell value mismatch")
        EXTERNAL_COMMAND_ERROR = "external_command_error", _("External command failed")
        ACCOUNT_NOT_FOUND = "account_not_found", _("Account not found")

    class Status(models.TextChoices):
        SCANNED = "scanned", _("Scanned")
        ACCEPTED = "accepted", _("Accepted")
        REJECTED = "rejected", _("Rejected")

    rfid = models.CharField(_("RFID"), max_length=255, blank=True)
    label = models.ForeignKey(
        "cards.RFID",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attempts",
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.SCANNED
    )
    authenticated = models.BooleanField(null=True, blank=True)
    allowed = models.BooleanField(null=True, blank=True)
    source = models.CharField(max_length=32, choices=Source.choices, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    attempted_at = models.DateTimeField(auto_now_add=True)
    charger = models.ForeignKey(
        "ocpp.Charger",
        on_delete=models.CASCADE,
        related_name="rfid_attempts",
        null=True,
        blank=True,
    )
    account = models.ForeignKey(
        "energy.CustomerAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rfid_attempts",
    )
    transaction = models.ForeignKey(
        "ocpp.Transaction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rfid_attempts",
    )

    class Meta:
        ordering = ["-attempted_at"]
        verbose_name = _("RFID Attempt")
        verbose_name_plural = _("RFID Attempts")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        status = self.get_status_display() or ""
        tag = self.rfid or "-"
        return f"{tag} ({status})"

    @classmethod
    def record_attempt(
        cls,
        payload: dict[str, Any],
        *,
        source: str,
        status: str | None = None,
        authenticated: bool | None = None,
        charger_id: int | None = None,
        account_id: int | None = None,
        transaction_id: int | None = None,
    ) -> "RFIDAttempt | None":
        rfid_value = str(payload.get("rfid", "") or "").strip().upper()
        if not rfid_value:
            return None
        normalized_status = status or cls.Status.SCANNED
        if authenticated is None:
            if normalized_status == cls.Status.ACCEPTED:
                authenticated = True
            elif normalized_status == cls.Status.REJECTED:
                authenticated = False
        label_id = payload.get("label_id")
        if label_id:
            try:
                label_id = int(label_id)
            except (TypeError, ValueError):
                label_id = None
        if label_id:
            label_model = cls._meta.get_field("label").remote_field.model
            if not label_model.objects.filter(pk=label_id).exists():
                label_id = None
        allowed_value = payload.get("allowed") if "allowed" in payload else None
        return cls.objects.create(
            rfid=rfid_value,
            label_id=label_id,
            status=normalized_status,
            authenticated=authenticated,
            allowed=allowed_value,
            source=source,
            payload=payload,
            charger_id=charger_id,
            account_id=account_id,
            transaction_id=transaction_id,
        )
