from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity
from apps.energy.models.billing import EnergyTariff


class Location(Entity):
    """Physical location available for business operations."""

    name = models.CharField(max_length=200)
    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
    )
    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
    )
    zone = models.CharField(
        max_length=3,
        choices=EnergyTariff.Zone.choices,
        blank=True,
        help_text=_("CFE climate zone used to select matching energy tariffs."),
    )
    contract_type = models.CharField(
        max_length=16,
        choices=EnergyTariff.ContractType.choices,
        blank=True,
        help_text=_("CFE service contract type required to match energy tariff pricing."),
    )
    address_line1 = models.CharField(
        _("Street address"),
        max_length=255,
        blank=True,
        default="",
        help_text=_("Primary street address or location description."),
    )
    address_line2 = models.CharField(
        _("Street address line 2"),
        max_length=255,
        blank=True,
        default="",
        help_text=_("Additional address information such as suite or building."),
    )
    city = models.CharField(
        _("City"),
        max_length=128,
        blank=True,
        default="",
    )
    state = models.CharField(
        _("State / Province"),
        max_length=128,
        blank=True,
        default="",
    )
    postal_code = models.CharField(
        _("Postal code"),
        max_length=32,
        blank=True,
        default="",
    )
    country = models.CharField(
        _("Country"),
        max_length=64,
        blank=True,
        default="",
    )
    phone_number = models.CharField(
        _("Phone number"),
        max_length=32,
        blank=True,
        default="",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_locations",
        verbose_name=_("Assigned to"),
        help_text=_("Optional user responsible for this location."),
    )

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

    class Meta:
        verbose_name = _("Location")
        verbose_name_plural = _("Locations")
        db_table = "core_location"


class GoogleMapsLocation(Entity):
    """Map location metadata synchronized with Google Maps."""

    location = models.OneToOneField(
        Location,
        on_delete=models.CASCADE,
        related_name="google_maps_details",
        verbose_name=_("Location"),
    )
    place_id = models.CharField(
        _("Place ID"),
        max_length=255,
        unique=True,
        help_text=_("Google Maps place identifier for the location."),
    )
    map_url = models.URLField(
        _("Map URL"),
        blank=True,
        default="",
        help_text=_("Public map URL returned by Google Maps."),
    )
    embed_url = models.URLField(
        _("Embed URL"),
        blank=True,
        default="",
        help_text=_("Embeddable map URL for dashboards and reports."),
    )
    formatted_address = models.CharField(
        _("Formatted address"),
        max_length=255,
        blank=True,
        default="",
        help_text=_("Normalized address provided by Google Maps."),
    )
    coordinates = models.JSONField(
        _("Coordinates"),
        null=True,
        blank=True,
        help_text=_("Latitude/longitude and viewport details from Google Maps."),
    )

    class Meta:
        verbose_name = _("Google Maps location")
        verbose_name_plural = _("Google Maps locations")
        db_table = "maps_google_maps_location"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.location.name} ({self.place_id})"
