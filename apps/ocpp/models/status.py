"""Firmware, diagnostics, and log status records from OCPP messages."""

from django.db import models

from apps.ocpp.models.assets import Charger


class OperationalStatusRecord(models.Model):
    class Kind(models.TextChoices):
        DIAGNOSTICS = "diagnostics", "Diagnostics"
        FIRMWARE = "firmware", "Firmware"
        LOG = "log", "Log"

    charger = models.ForeignKey(
        Charger, on_delete=models.CASCADE, related_name="operational_statuses"
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    status = models.CharField(max_length=80)
    payload = models.JSONField(default=dict)
    occurred_at = models.DateTimeField(auto_now_add=True)
