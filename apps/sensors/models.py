from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import re

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity, EntityManager
from apps.recipes.models import Recipe


class PhysicalSensor(Entity):
    """Abstract base for physical sensors that parse readings from reports."""

    name = models.CharField(max_length=128)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)
    unit = models.CharField(max_length=16, blank=True)
    report_regex = models.CharField(
        max_length=255,
        blank=True,
        help_text=_(
            "Regex used to parse sensor readings from reports. Use a named "
            "group 'value' or the first capture group."
        ),
    )
    report_scale = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        default=Decimal("1"),
        help_text=_("Multiplier applied to parsed readings."),
    )
    display_precision = models.PositiveSmallIntegerField(
        default=1, help_text=_("Number of decimal places to display for readings.")
    )
    sampling_interval_seconds = models.PositiveIntegerField(
        default=300,
        validators=[MinValueValidator(1)],
        help_text=_("Sampling interval in seconds."),
    )
    last_reading = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True
    )
    last_read_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        abstract = True
        ordering = ["name"]

    def extract_reading(self, report: str | None) -> Decimal | None:
        if not report or not self.report_regex:
            return None

        match = re.search(self.report_regex, report, flags=re.IGNORECASE | re.MULTILINE)
        if not match:
            return None

        value = match.groupdict().get("value")
        if value is None:
            try:
                value = match.group(1)
            except IndexError:
                value = None
        if value is None:
            return None

        try:
            reading = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None

        return reading * self.report_scale

    def update_from_report(self, report: str, *, commit: bool = True) -> Decimal | None:
        reading = self.extract_reading(report)
        if reading is None:
            return None

        self.last_reading = reading
        self.last_read_at = timezone.now()
        if commit:
            self.save(update_fields=["last_reading", "last_read_at"])
        return reading

    def format_reading(self, reading: Decimal | None = None) -> str:
        if reading is None:
            reading = self.last_reading
        if reading is None:
            return ""

        precision = max(self.display_precision, 0)
        value = f"{reading:.{precision}f}"
        unit = self.unit or ""
        return f"{value}{unit}".strip()


class ThermometerManager(EntityManager):
    def get_by_natural_key(self, slug: str):  # pragma: no cover - fixture loader
        return self.get(slug=slug)


class Thermometer(PhysicalSensor):
    """Physical thermometer sensor readings."""

    objects = ThermometerManager()

    class Meta(PhysicalSensor.Meta):
        verbose_name = _("Thermometer")
        verbose_name_plural = _("Thermometers")

    def natural_key(self):  # pragma: no cover - fixture loader
        return (self.slug,)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

    def format_lcd_reading(self) -> str:
        return self.format_reading()

    def record_reading(
        self,
        reading: Decimal,
        *,
        read_at: datetime | None = None,
        commit: bool = True,
    ) -> None:
        read_at = read_at or timezone.now()
        self.last_reading = reading
        self.last_read_at = read_at
        if commit:
            self.save(update_fields=["last_reading", "last_read_at"])
            ThermometerReading.objects.create(
                thermometer=self, reading=reading, read_at=read_at
            )

    def update_from_report(self, report: str, *, commit: bool = True) -> Decimal | None:
        reading = self.extract_reading(report)
        if reading is None:
            return None
        self.record_reading(reading, commit=commit)
        return reading


class ThermometerReading(models.Model):
    thermometer = models.ForeignKey(
        Thermometer, related_name="readings", on_delete=models.CASCADE
    )
    reading = models.DecimalField(max_digits=8, decimal_places=2)
    read_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-read_at"]
        indexes = [models.Index(fields=["thermometer", "read_at"])]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.thermometer} @ {self.read_at:%Y-%m-%d %H:%M:%S}"


class UsbTrackerManager(EntityManager):
    def get_by_natural_key(self, slug: str):  # pragma: no cover - fixture helper
        return self.get(slug=slug)


class UsbTracker(Entity):
    """Watch mounted USB devices for trigger files and run a recipe."""

    name = models.CharField(max_length=128)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)
    required_file_path = models.CharField(
        max_length=255,
        help_text=_("Relative path that must exist on the USB device."),
    )
    required_file_regex = models.TextField(
        blank=True,
        help_text=_(
            "Optional regex used to validate file contents before triggering."
        ),
    )
    recipe = models.ForeignKey(
        Recipe,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="usb_trackers",
    )
    cooldown_seconds = models.PositiveIntegerField(
        default=10,
        validators=[MinValueValidator(1)],
        help_text=_("Minimum seconds between triggers."),
    )
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_matched_at = models.DateTimeField(null=True, blank=True)
    last_triggered_at = models.DateTimeField(null=True, blank=True)
    last_match_path = models.CharField(max_length=512, blank=True)
    last_match_signature = models.CharField(max_length=256, blank=True)
    last_recipe_result = models.TextField(blank=True)
    last_error = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    objects = UsbTrackerManager()

    class Meta:
        ordering = ["name"]
        verbose_name = _("USB Tracker")
        verbose_name_plural = _("USB Trackers")

    def natural_key(self):  # pragma: no cover - fixture loader
        return (self.slug,)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name


__all__ = [
    "PhysicalSensor",
    "Thermometer",
    "ThermometerManager",
    "ThermometerReading",
    "UsbTracker",
    "UsbTrackerManager",
]
