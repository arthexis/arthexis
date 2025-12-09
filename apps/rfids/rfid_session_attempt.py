from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity
from apps.energy.models import CustomerAccount


class RFIDSessionAttempt(Entity):
    """Record RFID authorisation attempts received via StartTransaction."""

    class Status(models.TextChoices):
        ACCEPTED = "accepted", _("Accepted")
        REJECTED = "rejected", _("Rejected")

    charger = models.ForeignKey(
        "ocpp.Charger",
        on_delete=models.CASCADE,
        related_name="rfid_attempts",
        null=True,
        blank=True,
    )
    rfid = models.CharField(_("RFID"), max_length=255, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices)
    attempted_at = models.DateTimeField(auto_now_add=True)
    account = models.ForeignKey(
        CustomerAccount,
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
        verbose_name = _("RFID Session Attempt")
        verbose_name_plural = _("RFID Session Attempts")
        db_table = "ocpp_rfidsessionattempt"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        status = self.get_status_display() or ""
        tag = self.rfid or "-"
        return f"{tag} ({status})"
