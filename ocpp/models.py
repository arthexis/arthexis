from django.db import models
from integrate.models import Entity
from django.urls import reverse
from django.contrib.sites.models import Site
from django.conf import settings
from django.utils.translation import gettext_lazy as _

from refs.models import Reference
from accounts.models import Account


class Location(Entity):
    """Physical location shared by chargers."""

    name = models.CharField(max_length=200)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

    class Meta:
        verbose_name = _("Location")
        verbose_name_plural = _("Locations")


class Charger(Entity):
    """Known charge point with optional configuration."""

    charger_id = models.CharField(_("Serial Number"), max_length=100, unique=True)
    number = models.PositiveIntegerField(_("Charger Number"), default=1)
    config = models.JSONField(default=dict, blank=True)
    require_rfid = models.BooleanField(_("Require RFID"), default=False)
    last_heartbeat = models.DateTimeField(null=True, blank=True)
    last_meter_values = models.JSONField(default=dict, blank=True)
    temperature = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    temperature_unit = models.CharField(max_length=16, blank=True)
    reference = models.OneToOneField(Reference, null=True, blank=True, on_delete=models.SET_NULL)
    location = models.ForeignKey(
        Location, null=True, blank=True, on_delete=models.SET_NULL, related_name="chargers"
    )
    last_path = models.CharField(max_length=255, blank=True)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.charger_id

    class Meta:
        verbose_name = _("Charger")
        verbose_name_plural = _("Chargers")

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
            ref, _ = Reference.objects.get_or_create(
                value=ref_value, defaults={"alt_text": self.charger_id}
            )
            self.reference = ref
            super().save(update_fields=["reference"])

    @property
    def name(self) -> str:
        if self.location:
            return f"{self.location.name} #{self.number}" if self.number else self.location.name
        return ""

    @property
    def latitude(self):
        return self.location.latitude if self.location else None

    @property
    def longitude(self):
        return self.location.longitude if self.location else None

    @property
    def total_kw(self) -> float:
        """Return total energy delivered by this charger in kW."""
        from . import store

        total = 0.0
        tx_active = store.transactions.get(self.charger_id)
        qs = self.transactions.all()
        if tx_active and tx_active.pk is not None:
            qs = qs.exclude(pk=tx_active.pk)
        for tx in qs:
            kw = tx.kw
            if kw:
                total += kw
        if tx_active:
            kw = tx_active.kw
            if kw:
                total += kw
        return total

    def purge(self):
        from . import store

        self.transactions.all().delete()
        self.meter_readings.all().delete()
        store.clear_log(self.charger_id, log_type="charger")
        store.transactions.pop(self.charger_id, None)
        store.history.pop(self.charger_id, None)

    def delete(self, *args, **kwargs):
        from django.db.models.deletion import ProtectedError
        from . import store

        if (
            self.transactions.exists()
            or self.meter_readings.exists()
            or store.get_logs(self.charger_id, log_type="charger")
            or store.transactions.get(self.charger_id)
            or store.history.get(self.charger_id)
        ):
            raise ProtectedError("Purge data before deleting charger.", [])
        super().delete(*args, **kwargs)


class Transaction(Entity):
    """Charging session data stored for each charger."""

    charger = models.ForeignKey(
        Charger, on_delete=models.CASCADE, related_name="transactions", null=True
    )
    account = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="transactions", null=True
    )
    rfid = models.CharField(max_length=20, blank=True)
    vin = models.CharField(max_length=17, blank=True)
    meter_start = models.IntegerField(null=True, blank=True)
    meter_stop = models.IntegerField(null=True, blank=True)
    start_time = models.DateTimeField()
    stop_time = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.charger}:{self.pk}"

    class Meta:
        verbose_name = _("Transaction")
        verbose_name_plural = _("Transactions")

    @property
    def kw(self) -> float:
        """Return consumed energy in kW for this session."""
        total = 0.0
        qs = self.meter_readings.filter(
            measurand__in=["", "Energy.Active.Import.Register"]
        ).order_by("timestamp")
        first = True
        for reading in qs:
            try:
                val = float(reading.value)
            except (TypeError, ValueError):  # pragma: no cover - unexpected
                continue
            if reading.unit != "kW":
                val = val / 1000.0
            if first and self.meter_start is not None:
                total += val - (self.meter_start / 1000.0)
                first = False
            else:
                total += val
                first = False
        if total == 0 and self.meter_start is not None and self.meter_stop is not None:
            total = (self.meter_stop - self.meter_start) / 1000.0
        if total < 0:
            return 0.0
        return total


class MeterReading(Entity):
    """Parsed meter values reported by chargers."""

    charger = models.ForeignKey(
        Charger, on_delete=models.CASCADE, related_name="meter_readings"
    )
    connector_id = models.IntegerField(null=True, blank=True)
    transaction = models.ForeignKey(
        Transaction,
        on_delete=models.CASCADE,
        related_name="meter_readings",
        null=True,
        blank=True,
    )
    timestamp = models.DateTimeField()
    measurand = models.CharField(max_length=100, blank=True)
    value = models.DecimalField(max_digits=12, decimal_places=3)
    unit = models.CharField(max_length=16, blank=True)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.charger} {self.measurand} {self.value}{self.unit}".strip()

    class Meta:
        verbose_name = _("Meter Reading")
        verbose_name_plural = _("Meter Readings")


class Simulator(Entity):
    """Preconfigured simulator that can be started from the admin."""

    name = models.CharField(max_length=100, unique=True)
    cp_path = models.CharField(max_length=100)
    host = models.CharField(max_length=100, default="127.0.0.1")
    ws_port = models.IntegerField(_("WS Port"), default=8000)
    rfid = models.CharField(max_length=8, default="FFFFFFFF")
    vin = models.CharField(max_length=17, blank=True)
    duration = models.IntegerField(default=600)
    interval = models.FloatField(default=5.0)
    pre_charge_delay = models.FloatField(_("Delay"), default=10.0)
    kw_max = models.FloatField(default=60.0)
    repeat = models.BooleanField(default=False)
    username = models.CharField(max_length=100, blank=True)
    password = models.CharField(max_length=100, blank=True)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

    class Meta:
        verbose_name = _("Simulator")
        verbose_name_plural = _("Simulators")

    def as_config(self):
        from .simulator import SimulatorConfig

        return SimulatorConfig(
            host=self.host,
            ws_port=self.ws_port,
            rfid=self.rfid,
            vin=self.vin,
            cp_path=self.cp_path,
            duration=self.duration,
            interval=self.interval,
            pre_charge_delay=self.pre_charge_delay,
            kw_max=self.kw_max,
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

