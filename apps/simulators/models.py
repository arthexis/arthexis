"""Models for simulator scheduling."""

from __future__ import annotations

from datetime import datetime, time

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity


class SimulatorSchedule(Entity):
    """Define when a simulator should run during the day."""

    name = models.CharField(
        max_length=120,
        blank=True,
        help_text=_("Optional label for this schedule."),
    )
    simulator = models.ForeignKey(
        "ocpp.Simulator",
        on_delete=models.CASCADE,
        related_name="schedules",
        help_text=_("Simulator configuration to run."),
    )
    active = models.BooleanField(
        default=True,
        help_text=_("Enable this schedule for the simulator."),
    )
    schedule_date = models.DateField(
        null=True,
        blank=True,
        help_text=_("Optional date for a one-off schedule; leave blank for daily runs."),
    )
    start_time = models.TimeField(
        default=time(0, 0),
        help_text=_("Start of the daily scheduling window."),
    )
    end_time = models.TimeField(
        default=time(23, 59),
        help_text=_("End of the daily scheduling window."),
    )
    run_count = models.PositiveSmallIntegerField(
        default=1,
        help_text=_("Number of simulator runs to schedule inside the window."),
    )
    randomize = models.BooleanField(
        default=False,
        help_text=_("Randomize run start times within the window."),
    )

    class Meta:
        verbose_name = _("Simulator Schedule")
        verbose_name_plural = _("Simulator Schedules")
        ordering = ["simulator", "schedule_date", "start_time"]

    def __str__(self) -> str:
        """Return a readable label for the schedule."""

        label = self.name.strip() if self.name else ""
        if label:
            return label
        date_suffix = (
            self.schedule_date.isoformat() if self.schedule_date else _("Daily")
        )
        return f"{self.simulator} ({date_suffix})"

    def clean(self) -> None:
        """Validate that the schedule window is within a single day."""

        super().clean()
        if self.end_time <= self.start_time:
            raise ValidationError(
                {"end_time": _("End time must be after the start time.")}
            )

    def window_minutes(self) -> int:
        """Return the number of minutes in the schedule window."""

        today = timezone.localdate()
        start = datetime.combine(today, self.start_time)
        end = datetime.combine(today, self.end_time)
        delta = end - start
        return int(delta.total_seconds() // 60)


__all__ = ["SimulatorSchedule"]
