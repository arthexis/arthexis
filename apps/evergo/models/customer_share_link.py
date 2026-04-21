"""Share links for Evergo customer pages."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone


class EvergoCustomerShareLink(models.Model):
    """Revocable share token that grants public access to one customer page."""

    share_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    customer = models.ForeignKey("evergo.EvergoCustomer", on_delete=models.CASCADE, related_name="share_links")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="evergo_customer_share_links_created",
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="evergo_customer_share_links_revoked",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Evergo Customer Share Link"
        verbose_name_plural = "Evergo Customer Share Links"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        """Return concise share label."""
        return f"{self.customer} · {self.share_id}"

    @property
    def is_active(self) -> bool:
        """Return whether this share token is currently active."""
        return self.revoked_at is None

    def revoke(self, *, actor=None) -> None:
        """Revoke this share link when it is still active."""
        if self.revoked_at is not None:
            return
        self.revoked_at = timezone.now()
        self.revoked_by = actor
        self.save(update_fields=["revoked_at", "revoked_by"])

    def get_absolute_url(self) -> str:
        """Return the public URL that resolves this share token."""
        return reverse("evergo:customer-shared-detail", kwargs={"share_id": self.share_id})
