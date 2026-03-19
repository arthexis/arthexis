"""Legacy Fitbit models retained only to support reversible migrations.

This module intentionally keeps the historical model definitions available while
existing deployments apply the schema-removal migration. The runtime Fitbit
integration code has been removed.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone


class FitbitConnection(models.Model):
    """Represent a previously connected Fitbit account for migration compatibility.

    Parameters:
        None.

    Returns:
        None.
    """

    name = models.CharField(max_length=64, unique=True)
    fitbit_user_id = models.CharField(max_length=64)
    device_id = models.CharField(max_length=64, blank=True)
    access_token = models.TextField()
    refresh_token = models.TextField(blank=True)
    token_expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        """Return a readable identity for admin and debugging output.

        Parameters:
            None.

        Returns:
            str: The connection label.
        """
        return f"{self.name} ({self.fitbit_user_id})"


class FitbitHealthSample(models.Model):
    """Represent historical Fitbit health payloads for migration compatibility.

    Parameters:
        None.

    Returns:
        None.
    """

    connection = models.ForeignKey(
        FitbitConnection,
        on_delete=models.CASCADE,
        related_name="health_samples",
    )
    resource = models.CharField(max_length=64)
    observed_at = models.DateTimeField(default=timezone.now)
    payload = models.JSONField(default=dict)
    polled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-observed_at", "-id"]


class FitbitNetMessageDelivery(models.Model):
    """Track historical Fitbit Net Message deliveries for migration compatibility.

    Parameters:
        None.

    Returns:
        None.
    """

    class Status(models.TextChoices):
        """Enumerate persisted delivery states for historical Fitbit rows.

        Parameters:
            None.

        Returns:
            None.
        """

        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"

    connection = models.ForeignKey(
        FitbitConnection,
        on_delete=models.CASCADE,
        related_name="net_message_deliveries",
    )
    net_message = models.ForeignKey(
        "nodes.NetMessage",
        on_delete=models.CASCADE,
        related_name="fitbit_deliveries",
    )
    rendered_text = models.CharField(max_length=256)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.QUEUED)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "net_message"],
                name="fitbit_unique_connection_message_delivery",
            )
        ]
