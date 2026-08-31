from __future__ import annotations

from .base import *


class CostUpdate(Entity):
    """Mid-session pricing update reported by the charge point."""

    charger = models.ForeignKey(
        "Charger",
        on_delete=models.CASCADE,
        related_name="cost_updates",
        verbose_name=_("Charger"),
    )
    transaction = models.ForeignKey(
        "Transaction",
        on_delete=models.SET_NULL,
        related_name="cost_updates",
        null=True,
        blank=True,
        verbose_name=_("Transaction"),
    )
    ocpp_transaction_id = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text=_("OCPP transaction identifier provided by the charge point."),
    )
    connector_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("Connector ID"),
    )
    total_cost = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name=_("Total cost"),
    )
    currency = models.CharField(max_length=8, blank=True, default="")
    payload = models.JSONField(default=dict, blank=True)
    reported_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-reported_at", "-created_at", "-pk"]
        verbose_name = _("Cost Update")
        verbose_name_plural = _("Cost Updates")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.charger} cost update"
