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


class CalculatorTemplate(models.Model):
    """Template containing parameters for an AWG calculation."""

    name = models.CharField(max_length=100)
    meters = models.PositiveIntegerField()
    amps = models.PositiveIntegerField(default=40)
    volts = models.PositiveIntegerField(default=220)
    material = models.CharField(max_length=2, default="cu")
    max_awg = models.CharField(max_length=5, blank=True)
    max_lines = models.PositiveIntegerField(default=1)
    phases = models.PositiveIntegerField(default=2)
    temperature = models.PositiveIntegerField(null=True, blank=True)
    conduit = models.CharField(max_length=10, blank=True)
    ground = models.PositiveIntegerField(default=1)

    def __str__(self):  # pragma: no cover - simple representation
        return self.name

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
