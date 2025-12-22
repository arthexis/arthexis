from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity, EntityManager


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


__all__ = [
    "PhysicalSensor",
    "Thermometer",
    "ThermometerManager",
]
