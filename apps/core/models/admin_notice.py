from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.base.models import Entity


class AdminNotice(Entity):
    """Administrative notices shown at the top of the admin dashboard."""

    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    dismissed_at = models.DateTimeField(null=True, blank=True)
    dismissed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dismissed_admin_notices",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Admin Notice"
        verbose_name_plural = "Admin Notices"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"Admin Notice {self.pk}"
