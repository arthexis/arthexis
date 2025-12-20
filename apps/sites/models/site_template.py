from __future__ import annotations

from django.core.validators import RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity

_HEX_COLOR_VALIDATOR = RegexValidator(
    regex=r"^#(?:[0-9a-fA-F]{3}){1,2}$",
    message="Enter a valid hex color code (e.g. #0d6efd).",
)


class SiteTemplateManager(models.Manager):
    def get_by_natural_key(self, name: str):
        return self.get(name=name)


class SiteTemplate(Entity):
    name = models.CharField(max_length=100, unique=True)
    primary_color = models.CharField(max_length=7, validators=[_HEX_COLOR_VALIDATOR])
    primary_color_emphasis = models.CharField(
        max_length=7, validators=[_HEX_COLOR_VALIDATOR]
    )
    accent_color = models.CharField(max_length=7, validators=[_HEX_COLOR_VALIDATOR])
    accent_color_emphasis = models.CharField(
        max_length=7, validators=[_HEX_COLOR_VALIDATOR]
    )
    support_color = models.CharField(max_length=7, validators=[_HEX_COLOR_VALIDATOR])
    support_color_emphasis = models.CharField(
        max_length=7, validators=[_HEX_COLOR_VALIDATOR]
    )
    support_text_color = models.CharField(
        max_length=7, validators=[_HEX_COLOR_VALIDATOR]
    )

    objects = SiteTemplateManager()

    class Meta:
        verbose_name = _("Site Branding")
        verbose_name_plural = _("Site Brandings")
        ordering = ("name",)

    def natural_key(self):  # pragma: no cover - simple representation
        return (self.name,)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

    @staticmethod
    def _hex_to_rgb(value: str) -> str:
        cleaned = value.lstrip("#")
        if len(cleaned) == 3:
            cleaned = "".join(ch * 2 for ch in cleaned)
        if len(cleaned) != 6:
            return ""
        try:
            r = int(cleaned[0:2], 16)
            g = int(cleaned[2:4], 16)
            b = int(cleaned[4:6], 16)
        except ValueError:
            return ""
        return f"{r}, {g}, {b}"

    @property
    def primary_rgb(self) -> str:
        return self._hex_to_rgb(self.primary_color)

    @property
    def accent_rgb(self) -> str:
        return self._hex_to_rgb(self.accent_color)

    @property
    def support_rgb(self) -> str:
        return self._hex_to_rgb(self.support_color)
