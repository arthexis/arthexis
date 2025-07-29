from django.db import models
from django.urls import reverse
from django.contrib.sites.models import Site
from django.conf import settings

from qrcodes.models import QRLink
from accounts.models import Account


class Charger(models.Model):
    """Known charge point with optional configuration."""

    charger_id = models.CharField("Serial Number", max_length=100, unique=True)
    name = models.CharField("Location Name", max_length=200, blank=True)
    config = models.JSONField(default=dict, blank=True)
    require_rfid = models.BooleanField("Require RFID", default=False)
    last_heartbeat = models.DateTimeField(null=True, blank=True)
    last_meter_values = models.JSONField(default=dict, blank=True)
    qr = models.OneToOneField(QRLink, null=True, blank=True, on_delete=models.SET_NULL)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    last_path = models.CharField(max_length=255, blank=True)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.charger_id

    def get_absolute_url(self):
        return reverse("charger-page", args=[self.charger_id])

    def _full_url(self) -> str:
        """Return absolute URL for the charger landing page."""
        domain = Site.objects.get_current().domain
        scheme = getattr(settings, "DEFAULT_HTTP_PROTOCOL", "http")
        return f"{scheme}://{domain}{self.get_absolute_url()}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        qr_value = self._full_url()
        if not self.qr or self.qr.value != qr_value:
            qr, _ = QRLink.objects.get_or_create(value=qr_value)
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


class MeterReading(models.Model):
    """Parsed meter values reported by chargers."""

    charger = models.ForeignKey(
        Charger, on_delete=models.CASCADE, related_name="meter_readings"
    )
    connector_id = models.IntegerField(null=True, blank=True)
    transaction_id = models.BigIntegerField(null=True, blank=True)
    timestamp = models.DateTimeField()
    measurand = models.CharField(max_length=100, blank=True)
    value = models.DecimalField(max_digits=12, decimal_places=3)
    unit = models.CharField(max_length=16, blank=True)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.charger} {self.measurand} {self.value}{self.unit}".strip()


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

