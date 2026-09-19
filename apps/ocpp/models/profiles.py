"""Charging-profile and charging-limit records."""

from django.db import models

from apps.ocpp.models.assets import Charger


class ChargingProfile(models.Model):
    charger = models.ForeignKey(
        Charger, on_delete=models.CASCADE, related_name="charging_profiles"
    )
    remote_id = models.CharField(max_length=80)
    stack_level = models.PositiveSmallIntegerField(default=0)
    purpose = models.CharField(max_length=60)
    kind = models.CharField(max_length=60)
    payload = models.JSONField(default=dict)
    active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("charger", "remote_id"), name="unique_charger_profile"
            )
        ]
