"""User-level flags and configuration switches."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class UserFlag(models.Model):
    """Store user-scoped flags that apply regardless of the active avatar."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="flags",
    )
    key = models.CharField(
        max_length=100,
        help_text=_("Unique key for this user-level configuration flag."),
    )
    is_enabled = models.BooleanField(
        default=True,
        db_default=True,
        help_text=_("Whether the flag is currently active."),
    )
    value = models.JSONField(
        null=True,
        blank=True,
        help_text=_("Optional JSON value attached to this flag."),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user__username", "key", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "key"],
                name="users_userflag_unique_key_per_user",
            )
        ]
        verbose_name = _("User Flag")
        verbose_name_plural = _("User Flags")

    def __str__(self) -> str:
        """Return concise identifier for admin tables."""

        return f"{self.user.username}:{self.key}"
