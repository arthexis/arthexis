from __future__ import annotations

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity


class Discovery(Entity):
    action_label = models.CharField(max_length=200)
    app_label = models.CharField(max_length=100)
    model_name = models.CharField(max_length=100, blank=True)
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="discoveries",
    )
    metadata = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Discovery")
        verbose_name_plural = _("Discoveries")

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"{self.action_label} ({self.created_at:%Y-%m-%d %H:%M})"


class DiscoveryItem(Entity):
    discovery = models.ForeignKey(
        Discovery, on_delete=models.CASCADE, related_name="items"
    )
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="discovery_items",
    )
    object_id = models.CharField(max_length=64, blank=True)
    label = models.CharField(max_length=255, blank=True)
    was_created = models.BooleanField(default=False)
    was_overwritten = models.BooleanField(default=False)
    data = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Discovery Item")
        verbose_name_plural = _("Discovery Items")

    def __str__(self) -> str:  # pragma: no cover - display helper
        label = self.label or self.object_id or "item"
        return f"{label} ({self.discovery_id})"
