from __future__ import annotations

from .base import *

class RFIDSessionAttempt(Entity):
    """Record RFID authorisation attempts received via StartTransaction."""

    class Status(models.TextChoices):
        ACCEPTED = "accepted", _("Accepted")
        REJECTED = "rejected", _("Rejected")

    charger = models.ForeignKey(
        "Charger",
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
        "Transaction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rfid_attempts",
    )

    class Meta:
        ordering = ["-attempted_at"]
        verbose_name = _("RFID Session Attempt")
        verbose_name_plural = _("RFID Session Attempts")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        status = self.get_status_display() or ""
        tag = self.rfid or "-"
        return f"{tag} ({status})"
