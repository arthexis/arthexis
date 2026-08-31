from __future__ import annotations

from .base import *


class ChargingStation(Ownable):
    """Physical charging station reported by a charge station websocket identity."""

    owner_required = False

    station_id = models.CharField(
        _("Station ID"),
        max_length=100,
        unique=True,
        help_text=_("Unique identifier reported by the charging station."),
    )
    display_name = models.CharField(
        _("Display Name"),
        max_length=200,
        blank=True,
        help_text=_("Optional friendly name shown in admin and public pages."),
    )
    last_path = models.CharField(max_length=255, blank=True)
    last_heartbeat = models.DateTimeField(null=True, blank=True)
    location = models.ForeignKey(
        Location,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="charging_stations",
    )
    station_model = models.ForeignKey(
        "StationModel",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="charging_stations",
        verbose_name=_("Station Model"),
        help_text=_("Optional hardware model for this charging station."),
    )

    owner_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="owned_charging_stations",
        help_text=_("Users who can view this charging station."),
    )
    owner_groups = models.ManyToManyField(
        SecurityGroup,
        blank=True,
        related_name="owned_charging_stations",
        help_text=_("Security groups that can view this charging station."),
    )

    class Meta:
        verbose_name = _("Charging Station")
        verbose_name_plural = _("Charging Stations")

    def __str__(self) -> str:
        """Return the station identifier."""

        return self.station_id

