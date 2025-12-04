from __future__ import annotations

from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity


class Language(Entity):
    """Supported interface language that can be referenced by other models."""

    code = models.SlugField(max_length=12, unique=True)
    english_name = models.CharField(max_length=100)
    native_name = models.CharField(max_length=100, blank=True)
    is_default = models.BooleanField(default=False)

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

    @classmethod
    def default(cls) -> "Language | None":
        return cls.objects.filter(is_default=True).first()


class Documentation(Entity):
    """Logical document that supports translations and source tracking."""

    slug = models.SlugField(max_length=100, unique=True)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    default_language = models.ForeignKey(
        Language,
        on_delete=models.PROTECT,
        related_name="default_documents",
        verbose_name=_("Default language"),
    )

    class Meta:
        ordering = ["slug"]
        verbose_name = _("Documentation")
        verbose_name_plural = _("Documentation")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.title


class DocumentationTranslation(Entity):
    """Language-specific source reference for a :class:`Documentation` object."""

    documentation = models.ForeignKey(
        Documentation,
        on_delete=models.CASCADE,
        related_name="translations",
    )
    language = models.ForeignKey(
        Language,
        on_delete=models.PROTECT,
        related_name="documentation_translations",
    )
    source_path = models.CharField(
        max_length=255,
        blank=True,
        help_text=_("Repository path to the localized document."),
    )
    body = models.TextField(
        blank=True,
        help_text=_("Optional cached content for the localized document."),
    )

    class Meta:
        ordering = ["documentation__slug", "language__code"]
        unique_together = ("documentation", "language")
        verbose_name = _("Documentation Translation")
        verbose_name_plural = _("Documentation Translations")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.documentation.slug} ({self.language.code})"


__all__ = ["Documentation", "DocumentationTranslation", "Language"]
