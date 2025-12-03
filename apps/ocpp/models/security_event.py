from __future__ import annotations

from .base import *

class SecurityEvent(Entity):
    """Security-related events reported by a charge point."""

    charger = models.ForeignKey(
        "Charger",
        on_delete=models.CASCADE,
        related_name="security_events",
    )
    event_type = models.CharField(_("Event Type"), max_length=120)
    event_timestamp = models.DateTimeField(_("Event Timestamp"))
    trigger = models.CharField(max_length=120, blank=True, default="")
    tech_info = models.TextField(blank=True, default="")
    sequence_number = models.BigIntegerField(null=True, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    reported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-event_timestamp", "-pk"]
        verbose_name = _("Security Event")
        verbose_name_plural = _("Security Events")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.charger}: {self.event_type}"
