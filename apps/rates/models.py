from __future__ import annotations

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity


class RateLimit(Entity):
    """Configurable rate limit rule for a given model type."""

    name = models.CharField(max_length=128, blank=True, default="")
    content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, null=True, blank=True
    )
    scope_key = models.CharField(
        max_length=64,
        default="default",
        help_text=_("Namespace used to differentiate rate limit scopes."),
    )
    limit = models.PositiveIntegerField(
        help_text=_("Maximum allowed attempts within the configured window."),
    )
    window_seconds = models.PositiveIntegerField(
        help_text=_("Rolling window in seconds for the rate limit."),
    )
    enabled = models.BooleanField(default=True)

    target_object_id = models.PositiveBigIntegerField(null=True, blank=True)
    target_object = GenericForeignKey("content_type", "target_object_id")

    class Meta:
        unique_together = ("content_type", "scope_key")
        verbose_name = "Rate limit"
        verbose_name_plural = "Rate limits"

    @classmethod
    def for_target(cls, target: object | None, scope_key: str = "default") -> "RateLimit | None":
        """Return the active rate limit rule for the given target and scope."""

        if target is None:
            return cls.objects.filter(
                content_type__isnull=True, scope_key=scope_key, enabled=True
            ).first()
        model = target if isinstance(target, type) else target.__class__
        content_type = ContentType.objects.get_for_model(model)
        return cls.objects.filter(
            content_type=content_type, scope_key=scope_key, enabled=True
        ).first()

    def cache_key(self, identifier: str) -> str:
        return f"rate-limit:{self.pk}:{self.scope_key}:{identifier}"
