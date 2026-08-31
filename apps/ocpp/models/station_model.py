from __future__ import annotations

from django.core.validators import MaxValueValidator, MinValueValidator

from .base import *

class StationModelManager(EntityManager):
    def get_by_natural_key(self, vendor: str, model_family: str, model: str):
        return self.get(vendor=vendor, model_family=model_family, model=model)

class StationModel(Entity):
    """Supported EVCS hardware model."""

    vendor = models.CharField(_("Vendor"), max_length=100)
    model_family = models.CharField(_("Model Family"), max_length=100)
    model = models.CharField(_("Model"), max_length=100)
    max_power_kw = models.DecimalField(
        _("Max Power (kW)"),
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_("Maximum sustained charging power supported by this model."),
    )
    max_voltage_v = models.PositiveIntegerField(
        _("Max Voltage (V)"),
        null=True,
        blank=True,
        help_text=_("Maximum supported operating voltage."),
    )
    preferred_ocpp_version = models.CharField(
        _("Preferred OCPP Version"),
        max_length=16,
        blank=True,
        default="",
        help_text=_(
            "Optional OCPP protocol version usually paired with this EVCS model."
        ),
    )
    connector_type = models.CharField(
        _("Connector Type"),
        max_length=64,
        blank=True,
        help_text=_("Primary connector format supported by this model."),
    )
    integration_rating = models.PositiveSmallIntegerField(
        _("Integration Rating"),
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(5)],
        help_text=_("Integration quality rating from 0 (unknown) to 5."),
    )
    instructions_markdown = models.TextField(
        blank=True,
        default="",
        help_text=_("Special instructions in Markdown format."),
    )
    images_bucket = models.ForeignKey(
        "media.MediaBucket",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="station_model_images",
        help_text=_("Media bucket for charger images."),
    )
    documents_bucket = models.ForeignKey(
        "media.MediaBucket",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="station_model_documents",
        help_text=_("Media bucket for manuals and supporting files."),
    )
    notes = models.TextField(
        blank=True,
        default="",
        help_text=_("Optional comments about capabilities or certifications."),
    )

    objects = StationModelManager()

    class Meta:
        unique_together = ("vendor", "model_family", "model")
        verbose_name = _("Station Model")
        verbose_name_plural = _("Station Models")
        db_table = "core_stationmodel"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        parts = [self.vendor]
        if self.model_family:
            parts.append(self.model_family)
        if self.model:
            parts.append(self.model)
        return " ".join(part for part in parts if part)

    def natural_key(self):  # pragma: no cover - simple representation
        return (self.vendor, self.model_family, self.model)


class StationModelConfigurationGuide(Entity):
    """Step-by-step guide for configuring a supported station model firmware."""

    station_model = models.ForeignKey(
        "ocpp.StationModel",
        on_delete=models.CASCADE,
        related_name="configuration_guides",
    )
    title = models.CharField(max_length=200)
    firmware_version = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text=_("Optional firmware version this guide targets."),
    )
    sort_order = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "core_stationmodelconfigurationguide"
        ordering = ("sort_order", "id")
        unique_together = ("station_model", "title", "firmware_version")
        verbose_name = _("Station Model Configuration Guide")
        verbose_name_plural = _("Station Model Configuration Guides")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        if self.firmware_version:
            return f"{self.title} ({self.firmware_version})"
        return self.title


class StationModelConfigurationGuideStep(Entity):
    """Single step inside a station model configuration guide."""

    guide = models.ForeignKey(
        "ocpp.StationModelConfigurationGuide",
        on_delete=models.CASCADE,
        related_name="steps",
    )
    step_number = models.PositiveIntegerField()
    title = models.CharField(max_length=200, blank=True, default="")
    instructions_markdown = models.TextField()

    class Meta:
        db_table = "core_stationmodelconfigurationguidestep"
        ordering = ("step_number", "id")
        verbose_name = _("Station Model Configuration Guide Step")
        verbose_name_plural = _("Station Model Configuration Guide Steps")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        label = self.title or _("Step")
        return f"{label} #{self.step_number}"
