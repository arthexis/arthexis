from __future__ import annotations

from django.contrib.sites.models import Site
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity
from apps.modules.models import Module


class SiteModuleVisibility(Entity):
    """Per-site navigation visibility override for a module."""

    VISIBILITY_SHOW = "show"
    VISIBILITY_HIDE = "hide"
    VISIBILITY_CHOICES = (
        (VISIBILITY_SHOW, _("Show")),
        (VISIBILITY_HIDE, _("Hide")),
    )

    AUDIENCE_ALL = "all"
    AUDIENCE_ANONYMOUS = "anonymous"
    AUDIENCE_AUTHENTICATED = "authenticated"
    AUDIENCE_CHOICES = (
        (AUDIENCE_ALL, _("All visitors")),
        (AUDIENCE_ANONYMOUS, _("Anonymous visitors")),
        (AUDIENCE_AUTHENTICATED, _("Authenticated users")),
    )

    site = models.ForeignKey(
        Site,
        on_delete=models.CASCADE,
        related_name="module_visibility_rules",
        verbose_name=_("Site"),
    )
    module = models.ForeignKey(
        Module,
        on_delete=models.CASCADE,
        related_name="site_visibility_rules",
        verbose_name=_("Module"),
        limit_choices_to={"is_deleted": False},
    )
    visibility = models.CharField(
        max_length=10,
        choices=VISIBILITY_CHOICES,
        default=VISIBILITY_SHOW,
        db_index=True,
    )
    audience = models.CharField(
        max_length=20,
        choices=AUDIENCE_CHOICES,
        default=AUDIENCE_ALL,
        db_index=True,
    )
    is_enabled = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ("site__domain", "module__priority", "module__path", "audience")
        verbose_name = _("Site module visibility")
        verbose_name_plural = _("Site module visibility rules")
        constraints = [
            models.UniqueConstraint(
                fields=("site", "module", "audience"),
                condition=models.Q(is_deleted=False),
                name="unique_site_module_visibility_audience",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.site.domain}: {self.get_visibility_display()} {self.module} ({self.get_audience_display()})"
