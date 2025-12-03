from __future__ import annotations

from .base import *
from .cp_network_profile import CPNetworkProfile

class CPNetworkProfileDeployment(Entity):
    """Track SetNetworkProfile deployments for specific charge points."""

    network_profile = models.ForeignKey(
        CPNetworkProfile,
        on_delete=models.CASCADE,
        related_name="deployments",
        verbose_name=_("Network profile"),
    )
    charger = models.ForeignKey(
        "Charger",
        on_delete=models.PROTECT,
        related_name="network_profile_deployments",
        verbose_name=_("Charge point"),
    )
    node = models.ForeignKey(
        Node,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="network_profile_deployments",
        verbose_name=_("Node"),
    )
    ocpp_message_id = models.CharField(
        _("OCPP message ID"), max_length=64, blank=True
    )
    status = models.CharField(_("Status"), max_length=32, blank=True)
    status_info = models.CharField(_("Status details"), max_length=255, blank=True)
    status_timestamp = models.DateTimeField(_("Status timestamp"), null=True, blank=True)
    requested_at = models.DateTimeField(_("Requested at"), auto_now_add=True)
    completed_at = models.DateTimeField(_("Completed at"), null=True, blank=True)
    request_payload = models.JSONField(default=dict, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-requested_at"]
        verbose_name = _("CP Network Profile Deployment")
        verbose_name_plural = _("CP Network Profile Deployments")
        indexes = [
            models.Index(fields=["ocpp_message_id"]),
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        if self.pk:
            return f"{self.network_profile} → {self.charger}"
        return "Network profile deployment"

    def mark_status(
        self,
        status_value: str,
        status_info: str = "",
        timestamp: datetime | None = None,
        *,
        response: dict | None = None,
    ) -> None:
        if timestamp is None:
            timestamp = timezone.now()
        updates = {
            "status": status_value,
            "status_info": status_info,
            "status_timestamp": timestamp,
        }
        if response is not None:
            updates["response_payload"] = response
        CPNetworkProfileDeployment.objects.filter(pk=self.pk).update(**updates)
        for field, value in updates.items():
            setattr(self, field, value)
