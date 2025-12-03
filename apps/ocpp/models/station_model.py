from __future__ import annotations

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
