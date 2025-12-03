from __future__ import annotations

from .base import *

class MeterValue(Entity):
    """Parsed meter values reported by chargers."""

    charger = models.ForeignKey(
        "Charger", on_delete=models.CASCADE, related_name="meter_values"
    )
    connector_id = models.PositiveIntegerField(null=True, blank=True)
    transaction = models.ForeignKey(
        "Transaction",
        on_delete=models.CASCADE,
        related_name="meter_values",
        null=True,
        blank=True,
    )
    timestamp = models.DateTimeField()
    context = models.CharField(max_length=32, blank=True)
    energy = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    voltage = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True
    )
    current_import = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True
    )
    current_offered = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True
    )
    temperature = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True
    )
    soc = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.charger} {self.timestamp}"

    @property
    def value(self):
        return self.energy

    @value.setter
    def value(self, new_value):
        self.energy = new_value

    class Meta:
        verbose_name = _("Meter Value")
        verbose_name_plural = _("Meter Values")
