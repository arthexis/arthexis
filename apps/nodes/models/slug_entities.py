from __future__ import annotations

from django.db import models


class SlugEntityManager(models.Manager):
    """Base manager for slug-addressable entities."""

    def get_by_natural_key(self, slug: str):
        """Return an entity by its natural slug key."""
        return self.get(slug=slug)


class SlugDisplayNaturalKeyMixin:
    """Provide natural key and string display for slug/display models."""

    def natural_key(self):  # pragma: no cover - simple representation
        """Return the natural key for serialization."""
        return (self.slug,)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        """Return the display label for this entity."""
        return self.display
