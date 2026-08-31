from __future__ import annotations

from .base import *

class CPNetworkProfile(Entity):
    """Network profile that can be provisioned via SetNetworkProfile."""

    name = models.CharField(_("Name"), max_length=200)
    description = models.TextField(_("Description"), blank=True)
    configuration_slot = models.PositiveIntegerField(
        _("Configuration slot"), default=1, help_text=_("Target configurationSlot.")
    )
    connection_data = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("connectionData payload for SetNetworkProfile."),
    )
    apn = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Optional APN settings to include with the profile."),
    )
    vpn = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Optional VPN settings to include with the profile."),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "configuration_slot"]
        verbose_name = _("CP Network Profile")
        verbose_name_plural = _("CP Network Profiles")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

    def build_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "configurationSlot": self.configuration_slot,
            "connectionData": self.connection_data or {},
        }
        if self.apn:
            payload["apn"] = self.apn
        if self.vpn:
            payload["vpn"] = self.vpn
        return payload
