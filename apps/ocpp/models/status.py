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
    source_action = models.CharField(max_length=80, blank=True)
    payload = models.JSONField(default=dict)
    reported_at = models.DateTimeField(null=True, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True)


class ChargerTimelineProgress(models.Model):
    """Observed charger timeline progress, independent from transport health."""

    class State(models.TextChoices):
        UNKNOWN = "unknown", "Unknown"
        HISTORICAL = "historical", "Historical"
        CATCHING_UP = "catching_up", "Catching up"
        LIVE = "live", "Live"

    charger = models.OneToOneField(
        Charger, on_delete=models.CASCADE, related_name="timeline_progress"
    )
    state = models.CharField(max_length=16, choices=State.choices, default=State.UNKNOWN)
    newest_event_at = models.DateTimeField(null=True, blank=True)
    last_received_at = models.DateTimeField(null=True, blank=True)
    historical_events_seen = models.PositiveBigIntegerField(default=0)
    observed_events = models.PositiveBigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)
