from django.db import models
from django.urls import reverse
from django.contrib.sites.models import Site
from django.conf import settings

from references.models import Reference
from accounts.models import Account


class Charger(models.Model):
    """Known charge point with optional configuration."""

    charger_id = models.CharField("Serial Number", max_length=100, unique=True)
    name = models.CharField("Location Name", max_length=200, blank=True)
    config = models.JSONField(default=dict, blank=True)
    require_rfid = models.BooleanField("Require RFID", default=False)
    last_heartbeat = models.DateTimeField(null=True, blank=True)
    last_meter_values = models.JSONField(default=dict, blank=True)
    reference = models.OneToOneField(Reference, null=True, blank=True, on_delete=models.SET_NULL)
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
        ref_value = self._full_url()
        if not self.reference or self.reference.value != ref_value:
            ref, _ = Reference.objects.get_or_create(value=ref_value)
            self.reference = ref
            super().save(update_fields=["reference"])

    @property
    def total_kwh(self) -> float:
        """Return total energy delivered by this charger in kWh."""
        total = 0.0
        for tx in Transaction.objects.filter(
            charger_id=self.charger_id,
            meter_start__isnull=False,
            meter_stop__isnull=False,
        ):
            diff = tx.meter_stop - tx.meter_start
            if diff > 0:
                total += diff / 1000.0
        return total

    def purge(self):
        from . import store

        Transaction.objects.filter(charger_id=self.charger_id).delete()
        self.meter_readings.all().delete()
        store.clear_log(self.charger_id)
        store.transactions.pop(self.charger_id, None)
        store.history.pop(self.charger_id, None)

    def delete(self, *args, **kwargs):
        from django.db.models.deletion import ProtectedError
        from . import store

        if (
            Transaction.objects.filter(charger_id=self.charger_id).exists()
            or self.meter_readings.exists()
            or store.get_logs(self.charger_id)
            or store.transactions.get(self.charger_id)
            or store.history.get(self.charger_id)
        ):
            raise ProtectedError("Purge data before deleting charger.", [])
        super().delete(*args, **kwargs)


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

    @property
    def kwh(self) -> float | None:
        """Return consumed energy in kWh for this session."""
        if self.meter_start is None or self.meter_stop is None:
            return None
        diff = self.meter_stop - self.meter_start
        if diff < 0:
            return 0.0
        return diff / 1000.0


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
    interval = models.FloatField(default=5.0)
    kwh_max = models.FloatField(default=60.0)
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
            interval=self.interval,
            kwh_max=self.kwh_max,
            repeat=self.repeat,
            username=self.username or None,
            password=self.password or None,
        )

    @property
    def ws_url(self) -> str:  # pragma: no cover - simple helper
        path = self.cp_path
        if not path.endswith("/"):
            path += "/"
        return f"ws://{self.host}:{self.ws_port}/{path}"

