from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models.ownable import Ownable


class AdminBadge(Ownable):
    """Configurable admin header badge definition."""

    owner_required = False

    PART_LABEL = "label"
    PART_VALUE = "value"
    PART_CHOICES = ((PART_LABEL, _("Label")), (PART_VALUE, _("Value")))

    slug = models.SlugField(max_length=100, unique=True)
    name = models.CharField(max_length=120)
    label = models.CharField(max_length=32)
    value_query_path = models.CharField(
        max_length=255,
        help_text=_(
            "Dotted path to a callable returning badge data for the current request."
        ),
    )
    first_part = models.CharField(max_length=10, choices=PART_CHOICES, default=PART_LABEL)
    second_part = models.CharField(max_length=10, choices=PART_CHOICES, default=PART_VALUE)
    filled_color = models.CharField(max_length=7, default="#28a745")
    missing_color = models.CharField(max_length=7, default="#6c757d")
    is_enabled = models.BooleanField(default=True)
    priority = models.IntegerField(default=0)

    class Meta:
        ordering = ("priority", "pk")
        verbose_name = _("Admin Badge")
        verbose_name_plural = _("Admin Badges")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name
