"""Models for onboarding and gamification avatars."""

from __future__ import annotations

from django.conf import settings
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _


class Avatar(models.Model):
    """A user avatar used across onboarding, simulation, and support experiences."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="avatars",
    )
    nickname = models.CharField(
        max_length=150,
        blank=True,
        help_text=_("Optional display nickname shown before the username."),
    )
    is_active = models.BooleanField(
        default=False,
        db_default=False,
        help_text=_("Marks this as the user's active avatar."),
    )
    data = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Arbitrary avatar attributes for game experiences."),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user__username", "nickname", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(is_active=True),
                name="game_avatar_one_active_per_user",
            )
        ]
        verbose_name = _("Avatar")
        verbose_name_plural = _("Avatars")

    def __str__(self) -> str:
        """Return avatar label shown in admin/UI selectors."""

        if self.nickname:
            return f"{self.nickname} ({self.user.username})"
        return self.user.username

    def save(self, *args, **kwargs):
        """Persist the avatar and keep one active avatar per user."""

        with transaction.atomic():
            if self.is_active and self.user_id:
                type(self).objects.filter(user_id=self.user_id, is_active=True).exclude(
                    pk=self.pk
                ).update(is_active=False)
            super().save(*args, **kwargs)
