from __future__ import annotations

from .base import *

class ChargingProfileDispatch(Entity):
    """Track where a charging profile has been dispatched."""

    profile = models.ForeignKey(
        "ChargingProfile",
        on_delete=models.CASCADE,
        related_name="dispatches",
    )
    charger = models.ForeignKey(
        "Charger",
        on_delete=models.CASCADE,
        related_name="charging_profile_dispatches",
    )
    message_id = models.CharField(max_length=36, blank=True, default="")
    status = models.CharField(max_length=32, blank=True, default="")
    status_info = models.CharField(max_length=255, blank=True, default="")
    request_payload = models.JSONField(default=dict, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Charging Profile Dispatch")
        verbose_name_plural = _("Charging Profile Dispatches")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["profile", "charger", "message_id"],
                name="unique_charging_profile_dispatch_message",
            )
        ]

    def __str__(self):  # pragma: no cover - simple representation
        return f"{self.profile} -> {self.charger}" if self.charger else str(self.profile)
