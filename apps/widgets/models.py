from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth.models import Group
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity

logger = logging.getLogger(__name__)


class WidgetZone(Entity):
    """Logical placement for widgets within the admin UI."""

    ZONE_SIDEBAR = "sidebar"
    ZONE_APPLICATION = "application"

    slug = models.SlugField(max_length=100, unique=True)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)

    class Meta:
        verbose_name = _("Widget Zone")
        verbose_name_plural = _("Widget Zones")
        ordering = ("slug",)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name


class Widget(Entity):
    """Registered widget configuration."""

    slug = models.SlugField(max_length=100, unique=True)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    zone = models.ForeignKey(WidgetZone, on_delete=models.CASCADE, related_name="widgets")
    template_name = models.CharField(max_length=255)
    renderer_path = models.CharField(max_length=255)
    is_enabled = models.BooleanField(default=True)
    priority = models.IntegerField(default=0)

    class Meta:
        verbose_name = _("Widget")
        verbose_name_plural = _("Widgets")
        ordering = ("priority", "pk")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name


class WidgetProfile(Entity):
    """Visibility profile binding users or groups to a widget."""

    widget = models.ForeignKey(Widget, on_delete=models.CASCADE, related_name="profiles")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="widget_profiles",
    )
    group = models.ForeignKey(Group, on_delete=models.CASCADE, null=True, blank=True)
    is_enabled = models.BooleanField(default=True)

    class Meta:
        verbose_name = _("Widget Profile")
        verbose_name_plural = _("Widget Profiles")
        constraints = [
            models.CheckConstraint(
                condition=Q(user__isnull=False) | Q(group__isnull=False),
                name="widgets_profile_requires_target",
            ),
            models.UniqueConstraint(
                fields=["widget", "user"],
                name="widgets_unique_user_profile",
                condition=Q(user__isnull=False),
            ),
            models.UniqueConstraint(
                fields=["widget", "group"],
                name="widgets_unique_group_profile",
                condition=Q(group__isnull=False),
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        target = self.user or self.group
        return f"{self.widget} -> {target}" if target else str(self.widget)

    @classmethod
    def visible_for(cls, widget: Widget, user) -> bool:
        profiles = list(widget.profiles.all())
        if not profiles:
            return True
        if not user or not getattr(user, "is_authenticated", False):
            return False

        user_group_ids = set(user.groups.values_list("id", flat=True))
        matches: list[WidgetProfile] = []
        for profile in profiles:
            if profile.user_id and profile.user_id == user.id:
                matches.append(profile)
            elif profile.group_id and profile.group_id in user_group_ids:
                matches.append(profile)
        if not matches:
            return False
        return any(profile.is_enabled for profile in matches)


__all__ = ["WidgetZone", "Widget", "WidgetProfile"]
