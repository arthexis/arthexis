from __future__ import annotations

from .base import *


class ClearedChargingLimitEvent(Entity):
    """Record a cleared charging limit notification from a charge point."""

    charger = models.ForeignKey(
        "Charger", on_delete=models.CASCADE, related_name="cleared_charging_limits"
    )
    ocpp_message_id = models.CharField(max_length=120, blank=True, default="")
    evse_id = models.PositiveIntegerField(null=True, blank=True)
    charging_limit_source = models.CharField(max_length=120, blank=True, default="")
    raw_payload = models.JSONField(default=dict, blank=True)
    cleared_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-cleared_at", "-pk"]
        verbose_name = _("Cleared Charging Limit Event")
        verbose_name_plural = _("Cleared Charging Limit Events")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        label = self.charging_limit_source or "unknown"
        if self.evse_id is not None:
            return f"{self.charger} cleared {label} on EVSE {self.evse_id}"
        return f"{self.charger} cleared {label}"


__all__ = ["ClearedChargingLimitEvent"]
