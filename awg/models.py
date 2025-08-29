from django.db import models
from bind.models import Entity
from django.utils.translation import gettext_lazy as _


class CableSize(Entity):
    """AWG cable size specification."""

    awg_size = models.CharField(max_length=5)
    material = models.CharField(max_length=2)
    dia_in = models.FloatField()
    dia_mm = models.FloatField()
    area_kcmil = models.FloatField()
    area_mm2 = models.FloatField()
    k_ohm_km = models.FloatField()
    k_ohm_kft = models.FloatField()
    amps_60c = models.PositiveIntegerField()
    amps_75c = models.PositiveIntegerField()
    amps_90c = models.PositiveIntegerField()
    line_num = models.PositiveIntegerField()

    def __str__(self):  # pragma: no cover - simple representation
        return f"{self.awg_size} {self.material}"

    class Meta:
        verbose_name = _("Cable Size")
        verbose_name_plural = _("Cable Sizes")


class ConduitFill(Entity):
    """Maximum wires allowed in a conduit."""

    trade_size = models.CharField(max_length=10)
    conduit = models.CharField(max_length=10)
    awg_14 = models.PositiveIntegerField(null=True, blank=True)
    awg_12 = models.PositiveIntegerField(null=True, blank=True)
    awg_10 = models.PositiveIntegerField(null=True, blank=True)
    awg_8 = models.PositiveIntegerField(null=True, blank=True)
    awg_6 = models.PositiveIntegerField(null=True, blank=True)
    awg_4 = models.PositiveIntegerField(null=True, blank=True)
    awg_3 = models.PositiveIntegerField(null=True, blank=True)
    awg_2 = models.PositiveIntegerField(null=True, blank=True)
    awg_1 = models.PositiveIntegerField(null=True, blank=True)
    awg_0 = models.PositiveIntegerField(null=True, blank=True)
    awg_00 = models.PositiveIntegerField(null=True, blank=True)
    awg_000 = models.PositiveIntegerField(null=True, blank=True)
    awg_0000 = models.PositiveIntegerField(null=True, blank=True)

    def __str__(self):  # pragma: no cover - simple representation
        return f"{self.trade_size} {self.conduit}"

    class Meta:
        verbose_name = _("Conduit Fill")
        verbose_name_plural = _("Conduit Fills")


class CalculatorTemplate(Entity):
    """Template containing parameters for an AWG calculation."""

    name = models.CharField(max_length=100, blank=True)
    description = models.CharField(max_length=255, blank=True)
    meters = models.PositiveIntegerField(null=True, blank=True)
    amps = models.PositiveIntegerField(default=40, null=True, blank=True)
    volts = models.PositiveIntegerField(default=220, null=True, blank=True)
    material = models.CharField(max_length=2, default="cu", blank=True)
    max_awg = models.CharField(max_length=5, blank=True)
    max_lines = models.PositiveIntegerField(default=1, null=True, blank=True)
    phases = models.PositiveIntegerField(default=2, null=True, blank=True)
    temperature = models.PositiveIntegerField(null=True, blank=True)
    conduit = models.CharField(max_length=10, blank=True)
    ground = models.PositiveIntegerField(default=1, null=True, blank=True)
    show_in_pages = models.BooleanField(default=False, blank=True)

    def __str__(self):  # pragma: no cover - simple representation
        return self.name

    class Meta:
        verbose_name = _("Calculator Template")
        verbose_name_plural = _("Calculator Templates")

    def run(self):
        from .views import find_awg

        return find_awg(
            meters=self.meters,
            amps=self.amps,
            volts=self.volts,
            material=self.material,
            max_awg=self.max_awg or None,
            max_lines=self.max_lines,
            phases=self.phases,
            temperature=self.temperature,
            conduit=self.conduit or None,
            ground=self.ground,
        )

    def get_absolute_url(self):
        from django.urls import reverse
        from urllib.parse import urlencode

        params: dict[str, object] = {}
        for field in (
            "meters",
            "amps",
            "volts",
            "material",
            "max_lines",
            "phases",
            "ground",
        ):
            value = getattr(self, field)
            default = self._meta.get_field(field).default
            if value not in (None, "") and value != default:
                params[field] = value
        if self.max_awg:
            params["max_awg"] = self.max_awg
        if self.temperature is not None:
            params["temperature"] = self.temperature
        if self.conduit:
            params["conduit"] = self.conduit

        base = reverse("awg:calculator")
        return f"{base}?{urlencode(params)}" if params else base
