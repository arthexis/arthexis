"""RFID models."""

from django.db import models
from django.core.validators import RegexValidator
from django.apps import apps


class RFID(models.Model):
    """RFID tag that may be assigned to one or more accounts."""

    rfid = models.CharField(
        max_length=8,
        unique=True,
        verbose_name="RFID",
        validators=[
            RegexValidator(
                r"^[0-9A-Fa-f]{8}$",
                message="RFID must be 8 hexadecimal digits",
            )
        ],
    )
    allowed = models.BooleanField(default=True)
    added_on = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.rfid:
            self.rfid = self.rfid.upper()
        super().save(*args, **kwargs)
        if not self.allowed:
            self.accounts.clear()

    def __str__(self):  # pragma: no cover - simple representation
        return self.rfid

    @staticmethod
    def get_account_by_rfid(value):
        """Return the account associated with an RFID code if it exists."""
        Account = apps.get_model("accounts", "Account")
        return (
            Account.objects.filter(
                rfids__rfid=value.upper(), rfids__allowed=True
            )
            .first()
        )

    class Meta:
        verbose_name = "RFID"
        verbose_name_plural = "RFIDs"
        db_table = "accounts_rfid"
