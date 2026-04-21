"""Customer model for Evergo synchronization."""

from __future__ import annotations

import uuid

from django.db import models
from django.urls import reverse


class EvergoCustomer(models.Model):
    """Local cache of customer info sourced from Evergo sales-order payloads."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    user = models.ForeignKey("evergo.EvergoUser", on_delete=models.CASCADE, related_name="customers")
    remote_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True)
    phone_number = models.CharField(max_length=64, blank=True)
    address = models.CharField(max_length=512, blank=True)
    latest_so = models.CharField(max_length=64, blank=True)
    latest_order = models.ForeignKey(
        "evergo.EvergoOrder",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="customers",
    )
    latest_order_updated_at = models.DateTimeField(null=True, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    refreshed_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Evergo Customer"
        verbose_name_plural = "Evergo Customers"
        unique_together = (("user", "remote_id"),)
        ordering = ("-latest_order_updated_at", "name")

    def __str__(self) -> str:
        """Return a concise customer label."""
        if self.latest_so:
            return f"{self.name} ({self.latest_so})"
        return self.name

    def get_absolute_url(self) -> str:
        """Return the public-facing URL for this customer profile."""
        return reverse("evergo:customer-public-detail", kwargs={"public_id": self.public_id})
