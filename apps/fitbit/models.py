"""Models for Fitbit account connectivity, health history, and Net Message delivery."""

from __future__ import annotations

from django.db import models
from django.utils import timezone


class FitbitConnection(models.Model):
    """Represents a connected Fitbit account/device used by this Suite node."""

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
        """Return a readable identity for admin and command output."""
        return f"{self.name} ({self.fitbit_user_id})"

    def token_is_expired(self) -> bool:
        """Return ``True`` when the current access token has expired."""
        if self.token_expires_at is None:
            return False
        return self.token_expires_at <= timezone.now()


class FitbitHealthSample(models.Model):
    """Historical health payload captured from Fitbit polling queries."""

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

    def __str__(self) -> str:
        """Return a concise description of the sample."""
        return f"{self.connection.name}:{self.resource}@{self.observed_at.isoformat()}"


class FitbitNetMessageDelivery(models.Model):
    """Tracks Net Messages forwarded to connected Fitbit targets."""

    class Status(models.TextChoices):
        """Delivery states for Fitbit Net Message forwarding."""

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

    def __str__(self) -> str:
        """Return a concise delivery description."""
        return f"{self.connection.name} <- {self.net_message_id} ({self.status})"
