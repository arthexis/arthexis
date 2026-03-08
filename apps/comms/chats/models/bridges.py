from __future__ import annotations

from django.contrib.sites.models import Site
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity, EntityManager


class ChatBridgeManager(EntityManager):
    """Manager providing helpers for chat bridge lookups."""

    def for_site(self, site: Site | None):
        queryset = self.filter(is_enabled=True)
        if site and getattr(site, "pk", None):
            bridge = queryset.filter(site=site).first()
            if bridge:
                return bridge
        return queryset.filter(is_default=True).first()


class ChatBridge(Entity):
    """Base configuration for routing chat messages to external services."""

    site = models.ForeignKey(
        Site,
        on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)s_bridges",
        null=True,
        blank=True,
        help_text=_(
            "Restrict this bridge to a specific site. Leave blank to use it as a fallback."
        ),
    )
    is_enabled = models.BooleanField(
        default=True,
        help_text=_("Disable to stop forwarding chat messages to this bridge."),
    )
    is_default = models.BooleanField(
        default=False,
        help_text=_("Use as the fallback bridge when no site-specific configuration is defined."),
    )
    avatars = models.ManyToManyField(
        "chats.ChatAvatar",
        related_name="%(app_label)s_%(class)s_bridges",
        blank=True,
        help_text=_("Avatars allowed to use this bridge."),
    )

    objects = ChatBridgeManager()

    default_site_error_message = _("Default chat bridges cannot target a specific site.")

    class Meta:
        abstract = True
        ordering = ["site__domain", "pk"]

    def clean(self):
        super().clean()
        errors: dict[str, list[str]] = {}
        if self.is_default and self.site_id:
            errors.setdefault("is_default", []).append(self.default_site_error_message)
        if errors:
            raise ValidationError(errors)
