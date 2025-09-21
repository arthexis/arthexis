"""Helpers for site badge defaults and router-specific colors."""

from __future__ import annotations

from django.contrib.sites.models import Site
from django.core.exceptions import FieldDoesNotExist
from django.db import models

DEFAULT_SITE_BADGE_COLOR = "#28a745"
ROUTER_BADGE_COLOR = "#ff8c00"
ROUTER_NAMES = {"router"}
ROUTER_DOMAINS = {"router", "10.42.0.1"}


def ensure_site_default_badge_color_field() -> models.Field:
    """Attach the ``default_badge_color`` field to ``Site`` if missing."""

    try:
        return Site._meta.get_field("default_badge_color")
    except FieldDoesNotExist:
        field = models.CharField(
            "default badge color",
            max_length=7,
            default=DEFAULT_SITE_BADGE_COLOR,
            help_text=(
                "Hex color applied when a site lacks an explicit badge override."
            ),
        )
        Site.add_to_class("default_badge_color", field)
        return field
