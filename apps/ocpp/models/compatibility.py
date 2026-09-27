"""Durable evidence about charger protocol quirks and compatibility fallbacks."""

from django.db import models

from apps.ocpp.models.assets import Charger


class CompatibilityEvidence(models.Model):
    charger = models.ForeignKey(
        Charger,
        on_delete=models.CASCADE,
        related_name="compatibility_evidence",
        null=True,
        blank=True,
    )
    charger_identity = models.CharField(max_length=255, blank=True)
    kind = models.CharField(max_length=80)
    protocol = models.CharField(max_length=32, blank=True)
    unique_id = models.CharField(max_length=255, blank=True)
    action = models.CharField(max_length=120, blank=True)
    details = models.JSONField(default=dict)
    observed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=("charger", "kind", "observed_at"), name="ocpp_compat_charger_kind_idx"),
            models.Index(fields=("charger_identity", "kind", "observed_at"), name="ocpp_compat_identity_kind_idx"),
        ]
