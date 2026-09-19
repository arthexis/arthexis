"""Reservation records retained for both OCPP protocol surfaces."""

from django.db import models

from apps.ocpp.models.assets import Charger, Connector


class Reservation(models.Model):
    charger = models.ForeignKey(
        Charger, on_delete=models.CASCADE, related_name="reservations"
    )
    connector = models.ForeignKey(
        Connector,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reservations",
    )
    remote_id = models.CharField(max_length=80, unique=True)
    id_tag = models.CharField(max_length=40)
    expires_at = models.DateTimeField()
    status = models.CharField(max_length=30, default="pending")
    updated_at = models.DateTimeField(auto_now=True)
