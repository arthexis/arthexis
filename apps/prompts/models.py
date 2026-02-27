"""Models for persisting developer prompts and implementation plans."""

from __future__ import annotations

from django.db import models

from apps.core.entity import Entity


class StoredPromptManager(models.Manager):
    """Manager that supports fixture natural key loading."""

    def get_by_natural_key(self, slug: str):  # pragma: no cover - fixture helper
        """Return a stored prompt by slug."""

        return self.get(slug=slug)


class StoredPrompt(Entity):
    """Persist the original request, refined plan, and PR context."""

    slug = models.SlugField(max_length=120, unique=True)
    title = models.CharField(max_length=200)
    prompt_text = models.TextField()
    initial_plan = models.TextField(
        help_text="Refined implementation plan derived from the original request.",
    )
    pr_reference = models.CharField(
        max_length=120,
        help_text="Pull request identifier such as PR-123 or a URL.",
    )
    context = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional context, such as touched files or decision notes.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = StoredPromptManager()

    class Meta:
        ordering = ["-updated_at", "-pk"]
        verbose_name = "Stored Prompt"
        verbose_name_plural = "Stored Prompts"

    def natural_key(self):  # pragma: no cover - fixture helper
        """Return the fixture natural key tuple."""

        return (self.slug,)

    def __str__(self) -> str:  # pragma: no cover - display helper
        """Return the admin/display label."""

        return self.title


__all__ = ["StoredPrompt"]
