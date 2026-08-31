from __future__ import annotations

from .base import *
from .charging_profile import ChargingProfile

class PowerProjection(Entity):
    """Aggregated power schedules returned by GetCompositeSchedule."""

    charger = models.ForeignKey(
        "Charger",
        on_delete=models.CASCADE,
        related_name="power_projections",
        verbose_name=_("Charger"),
    )
    connector_id = models.PositiveIntegerField(
        default=0,
        verbose_name=_("Connector ID"),
        help_text=_("Connector targeted by the projection (0 for the EVCS)."),
    )
    duration_seconds = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("Duration (seconds)"),
    )
    charging_rate_unit = models.CharField(
        max_length=2,
        choices=ChargingProfile.RateUnit.choices,
        blank=True,
        default="",
        verbose_name=_("Charging rate unit"),
    )
    charging_schedule_periods = models.JSONField(
        default=list,
        blank=True,
        help_text=_("Composite schedule periods returned by the EVCS."),
    )
    schedule_start = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Schedule start"),
    )
    status = models.CharField(max_length=32, blank=True, default="")
    raw_response = models.JSONField(default=dict, blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    received_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-received_at", "-requested_at", "-pk"]
        verbose_name = _("Power Projection")
        verbose_name_plural = _("Power Projections")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        connector = self.connector_id or 0
        return f"{self.charger} [{connector}] projection"
