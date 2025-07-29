from django.db import models


class CableSize(models.Model):
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


class ConduitFill(models.Model):
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
