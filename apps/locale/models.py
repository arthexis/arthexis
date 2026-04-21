from __future__ import annotations

from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity, EntityManager


class LanguageManager(EntityManager):
    def get_by_natural_key(self, code: str):  # pragma: no cover - used by fixtures
        return self.get(code=code)


class Language(Entity):
    """Supported interface language that can be referenced by other models."""

    code = models.SlugField(max_length=12, unique=True)
    english_name = models.CharField(max_length=100)
    native_name = models.CharField(max_length=100, blank=True)
    is_default = models.BooleanField(default=False)

    objects = LanguageManager()

    class Meta:
        ordering = ["code"]
        verbose_name = _("Language")
        verbose_name_plural = _("Languages")
        constraints = [
            models.UniqueConstraint(
                fields=["is_default"],
                condition=Q(is_default=True),
                name="locale_language_single_default",
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        label = self.native_name or self.english_name or self.code
        return f"{label} ({self.code})" if label else self.code

    def natural_key(self):  # pragma: no cover - used by fixtures
        return (self.code,)

    @classmethod
    def default(cls) -> "Language | None":
        return cls.objects.filter(is_default=True).first()


__all__ = ["Language"]
