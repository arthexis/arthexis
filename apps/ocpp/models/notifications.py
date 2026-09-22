"""Retained event, display, customer, and monitoring notifications."""

from django.db import models

from apps.ocpp.models.assets import Charger


class NotificationRecord(models.Model):
    charger = models.ForeignKey(
        Charger, on_delete=models.CASCADE, related_name="ocpp_notifications"
    )
    action = models.CharField(max_length=80)
    payload = models.JSONField(default=dict)
    reported_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=("charger", "action", "received_at"))]


class MonitoringRecord(models.Model):
    charger = models.ForeignKey(
        Charger, on_delete=models.CASCADE, related_name="monitoring_records"
    )
    component = models.CharField(max_length=120, blank=True)
    variable = models.CharField(max_length=120, blank=True)
    severity = models.IntegerField(null=True, blank=True)
    event_type = models.CharField(max_length=80)
    payload = models.JSONField(default=dict)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=("charger", "event_type", "occurred_at"))]
