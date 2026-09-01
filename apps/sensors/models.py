from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
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
        """Extract and scale a sensor reading from a raw report string.

        Args:
            report: Raw device report text to inspect.

        Returns:
            The parsed decimal reading, or ``None`` when no reading matches.
        """
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
        """Update the sensor from a raw report.

        Args:
            report: Raw report text to parse.
            commit: When ``True``, persist the updated reading fields.

        Returns:
            The parsed reading, or ``None`` when parsing fails.
        """
        reading = self.extract_reading(report)
        if reading is None:
            return None

        self.last_reading = reading
        self.last_read_at = timezone.now()
        if commit:
            self.save(update_fields=["last_reading", "last_read_at"])
        return reading

    def format_reading(self, reading: Decimal | None = None) -> str:
        """Return a human-readable reading string.

        Args:
            reading: Optional reading override. Defaults to ``last_reading``.

        Returns:
            A formatted string with the configured precision and unit.
        """
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

    class Kind(models.TextChoices):
        AMBIENT = "ambient", _("Ambient")
        SOC = "soc", _("SoC/CPU")

    class AlarmLevel(models.TextChoices):
        NORMAL = "normal", _("Normal")
        WARNING = "warning", _("Warning")
        CRITICAL = "critical", _("Critical")

    kind = models.CharField(
        max_length=16,
        choices=Kind.choices,
        default=Kind.AMBIENT,
        db_index=True,
        help_text=_("Temperature source classification for display and history."),
    )
    alarm_enabled = models.BooleanField(
        default=False,
        help_text=_("Enable local temperature alarm evaluation for this thermometer."),
    )
    alarm_warning_threshold_c = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_("Warning threshold in degrees Celsius."),
    )
    alarm_critical_threshold_c = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_("Critical threshold in degrees Celsius."),
    )
    alarm_repeat_seconds = models.PositiveIntegerField(
        default=900,
        validators=[MinValueValidator(1)],
        help_text=_("Minimum seconds between repeated alarms at the same level."),
    )
    alarm_net_message_enabled = models.BooleanField(
        default=True,
        help_text=_("Broadcast NetMessages when alarm state changes."),
    )
    last_alarm_level = models.CharField(
        max_length=16,
        choices=AlarmLevel.choices,
        default=AlarmLevel.NORMAL,
        blank=True,
        help_text=_("Last emitted temperature alarm level for rate limiting."),
    )
    last_alarm_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Time of the last emitted temperature alarm event."),
    )

    objects = ThermometerManager()

    class Meta(PhysicalSensor.Meta):
        verbose_name = _("Thermometer")
        verbose_name_plural = _("Thermometers")
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(alarm_critical_threshold_c__isnull=True)
                    | models.Q(alarm_warning_threshold_c__isnull=True)
                    | models.Q(
                        alarm_critical_threshold_c__gte=models.F(
                            "alarm_warning_threshold_c"
                        )
                    )
                ),
                name="sensors_thermometer_alarm_threshold_order",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(alarm_enabled=False)
                    | models.Q(alarm_warning_threshold_c__isnull=False)
                    | models.Q(alarm_critical_threshold_c__isnull=False)
                ),
                name="sensors_thermometer_alarm_enabled_threshold",
            ),
        ]

    def natural_key(self):  # pragma: no cover - fixture loader
        return (self.slug,)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

    def clean(self) -> None:
        super().clean()
        if (
            self.alarm_warning_threshold_c is not None
            and self.alarm_critical_threshold_c is not None
            and self.alarm_critical_threshold_c < self.alarm_warning_threshold_c
        ):
            raise ValidationError(
                {
                    "alarm_critical_threshold_c": _(
                        "Critical threshold must be greater than or equal to the warning threshold."
                    )
                }
            )
        if (
            self.alarm_enabled
            and self.alarm_warning_threshold_c is None
            and self.alarm_critical_threshold_c is None
        ):
            raise ValidationError(
                {
                    "alarm_enabled": _(
                        "At least one alarm threshold must be set when alarms are enabled."
                    )
                }
            )

    def record_reading(
        self,
        reading: Decimal,
        *,
        read_at: datetime | None = None,
        commit: bool = True,
    ) -> None:
        """Persist a thermometer reading and append to the reading history.

        Args:
            reading: Parsed reading to store.
            read_at: Optional timestamp for the reading.
            commit: When ``True``, save the thermometer and create a reading row.

        Returns:
            ``None``.
        """
        read_at = read_at or timezone.now()
        self.last_reading = reading
        self.last_read_at = read_at
        if commit:
            self.save(update_fields=["last_reading", "last_read_at"])
            ThermometerReading.objects.create(
                thermometer=self, reading=reading, read_at=read_at
            )

    def update_from_report(self, report: str, *, commit: bool = True) -> Decimal | None:
        """Parse a report and store the resulting thermometer reading.

        Args:
            report: Raw report text to parse.
            commit: When ``True``, persist the reading and history row.

        Returns:
            The parsed reading, or ``None`` when parsing fails.
        """
        reading = self.extract_reading(report)
        if reading is None:
            return None
        self.record_reading(reading, commit=commit)
        return reading


class ThermometerReading(models.Model):
    """Historical point-in-time reading captured for a thermometer."""

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


class ThermometerAlarmEvent(models.Model):
    """Recorded temperature alarm state transition for a thermometer."""

    class Level(models.TextChoices):
        WARNING = "warning", _("Warning")
        CRITICAL = "critical", _("Critical")
        RECOVERY = "recovery", _("Recovery")

    thermometer = models.ForeignKey(
        Thermometer, related_name="alarm_events", on_delete=models.CASCADE
    )
    level = models.CharField(max_length=16, choices=Level.choices, db_index=True)
    reading = models.DecimalField(max_digits=8, decimal_places=2)
    threshold = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True
    )
    message = models.CharField(max_length=256, blank=True)
    net_message = models.ForeignKey(
        "nodes.NetMessage",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="temperature_alarm_events",
    )
    created = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created"]
        indexes = [
            models.Index(fields=["thermometer", "created"]),
            models.Index(fields=["thermometer", "level", "created"]),
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.thermometer} {self.level} @ {self.reading}"


class UsbTrackerManager(EntityManager):
    def get_by_natural_key(self, slug: str):  # pragma: no cover - fixture helper
        return self.get(slug=slug)


class UsbTracker(Entity):
    """Watch mounted USB devices for a required file and record passive match status."""

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
            "Optional regex used to validate file contents before marking a match."
        ),
    )
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_matched_at = models.DateTimeField(null=True, blank=True)
    last_match_path = models.CharField(max_length=512, blank=True)
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
