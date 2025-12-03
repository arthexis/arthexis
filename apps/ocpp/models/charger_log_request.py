from __future__ import annotations

from .base import *

def generate_log_request_id() -> int:
    """Return a random positive identifier suitable for OCPP log requests."""

    import secrets

    # Limit to 31 bits to remain compatible with OCPP integer fields.
    return secrets.randbits(31) or 1

class ChargerLogRequest(Entity):
    """Track GetLog interactions initiated against a charge point."""

    charger = models.ForeignKey(
        "Charger",
        on_delete=models.CASCADE,
        related_name="log_requests",
    )
    request_id = models.BigIntegerField(
        _("Request Id"), default=generate_log_request_id, unique=True
    )
    message_id = models.CharField(max_length=64, blank=True, default="")
    log_type = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=32, blank=True, default="")
    filename = models.CharField(max_length=255, blank=True, default="")
    location = models.CharField(max_length=500, blank=True, default="")
    session_key = models.CharField(max_length=200, blank=True, default="")
    requested_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    last_status_at = models.DateTimeField(null=True, blank=True)
    last_status_payload = models.JSONField(default=dict, blank=True)
    raw_response = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-requested_at", "-pk"]
        verbose_name = _("Log Request")
        verbose_name_plural = _("Log Requests")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        label = self.log_type or "Log"
        return f"{label} request {self.request_id}"
