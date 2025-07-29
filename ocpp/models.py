from django.db import models
from django.urls import reverse

from qrcodes.models import QRLink
from accounts.models import Account


class Charger(models.Model):
    """Known charge point with optional configuration."""

    charger_id = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=200, blank=True)
    config = models.JSONField(default=dict, blank=True)
    require_rfid = models.BooleanField(default=False)
    last_heartbeat = models.DateTimeField(null=True, blank=True)
    last_meter_values = models.JSONField(default=dict, blank=True)
    qr = models.OneToOneField(QRLink, null=True, blank=True, on_delete=models.SET_NULL)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.charger_id

    def get_absolute_url(self):
        return reverse("charger-page", args=[self.charger_id])

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.qr:
            qr, _ = QRLink.objects.get_or_create(value=self.get_absolute_url())
            self.qr = qr
            super().save(update_fields=["qr"])


class Transaction(models.Model):
    """Charging session data stored for each charger."""

    charger_id = models.CharField(max_length=100)
    transaction_id = models.BigIntegerField()
    account = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="transactions", null=True
    )
    meter_start = models.IntegerField(null=True, blank=True)
    meter_stop = models.IntegerField(null=True, blank=True)
    start_time = models.DateTimeField()
    stop_time = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("charger_id", "transaction_id")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.charger_id}:{self.transaction_id}"


class Simulator(models.Model):
    """Preconfigured simulator that can be started from the admin."""

    name = models.CharField(max_length=100, unique=True)
    cp_path = models.CharField(max_length=100)
    host = models.CharField(max_length=100, default="127.0.0.1")
    ws_port = models.IntegerField(default=8000)
    rfid = models.CharField(max_length=8, default="FFFFFFFF")
    repeat = models.BooleanField(default=False)
    username = models.CharField(max_length=100, blank=True)
    password = models.CharField(max_length=100, blank=True)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

    def as_config(self):
        from .simulator import SimulatorConfig

        return SimulatorConfig(
            host=self.host,
            ws_port=self.ws_port,
            rfid=self.rfid,
            cp_path=self.cp_path,
            repeat=self.repeat,
            username=self.username or None,
            password=self.password or None,
        )

