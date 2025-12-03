from __future__ import annotations

from .base import *
from .meter_value import MeterValue

class MeterReadingManager(EntityManager):
    def _normalize_kwargs(self, kwargs: dict) -> dict:
        normalized = dict(kwargs)
        value = normalized.pop("value", None)
        unit = normalized.pop("unit", None)
        charger = normalized.get("charger")
        charger_unit = getattr(charger, "energy_unit", None)
        if value is not None:
            energy = value
            try:
                energy = Decimal(value)
            except (InvalidOperation, TypeError, ValueError):
                energy = None
            if energy is not None:
                normalized.setdefault(
                    "energy",
                    Charger.normalize_energy_value(
                        energy, unit, default_unit=charger_unit
                    ),
                )
        normalized.pop("measurand", None)
        return normalized

    def create(self, **kwargs):
        return super().create(**self._normalize_kwargs(kwargs))

    def get_or_create(self, defaults=None, **kwargs):
        if defaults:
            defaults = self._normalize_kwargs(defaults)
        return super().get_or_create(
            defaults=defaults, **self._normalize_kwargs(kwargs)
        )

class MeterReading(MeterValue):
    """Proxy model for backwards compatibility."""

    objects = MeterReadingManager()

    class Meta:
        proxy = True
        verbose_name = _("Meter Value")
        verbose_name_plural = _("Meter Values")
