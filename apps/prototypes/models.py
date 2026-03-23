"""Models for retired prototype records retained as metadata."""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models

from apps.base.models import Entity


_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class Prototype(Entity):
    """Store historical metadata for retired prototype experiments.

    Parameters:
        Entity: Base model providing shared soft-delete and ownership flags.

    Returns:
        Prototype: Persisted prototype metadata row.

    Raised exceptions:
        ValidationError: Raised when ``slug`` or ``env_overrides`` are invalid.
    """

    slug = models.SlugField(
        max_length=80,
        unique=True,
        validators=[
            RegexValidator(
                regex=_SLUG_RE.pattern,
                message="Use lowercase snake_case starting with a letter.",
            )
        ],
        help_text="Stable prototype slug retained for historical reference.",
    )
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    app_module = models.CharField(
        max_length=255,
        unique=True,
        blank=True,
        help_text="Legacy hidden runtime module retained for historical reference.",
    )
    app_label = models.CharField(
        max_length=100,
        unique=True,
        blank=True,
        help_text="Legacy Django app label retained for historical reference.",
    )
    port = models.PositiveIntegerField(
        default=8890,
        help_text="Legacy backend port retained for historical reference.",
    )
    sqlite_path = models.CharField(
        max_length=255,
        blank=True,
        help_text="Legacy SQLite path retained for historical reference.",
    )
    sqlite_test_path = models.CharField(
        max_length=255,
        blank=True,
        help_text="Legacy test SQLite path retained for historical reference.",
    )
    cache_dir = models.CharField(
        max_length=255,
        blank=True,
        help_text="Legacy cache directory retained for historical reference.",
    )
    env_overrides = models.JSONField(
        default=dict,
        blank=True,
        help_text="Legacy environment overrides retained for historical reference.",
    )
    is_active = models.BooleanField(
        default=False,
        editable=False,
        help_text="Legacy activation flag kept only for historical compatibility.",
    )
    is_runnable = models.BooleanField(
        default=False,
        editable=False,
        help_text="Always false. Prototype runtime scaffolding has been retired.",
    )
    retired_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the prototype runtime workflow was retired for this record.",
    )
    retirement_notes = models.TextField(
        blank=True,
        help_text="Administrative notes about the retired prototype record.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "slug"]
        verbose_name = "Prototype"
        verbose_name_plural = "Prototypes"

    def __str__(self) -> str:
        """Return the human-readable prototype name.

        Parameters:
            None.

        Returns:
            str: The prototype display name.
        """

        return self.name

    def clean(self) -> None:
        """Validate metadata and force prototypes to remain inert.

        Parameters:
            None.

        Returns:
            None.

        Raised exceptions:
            ValidationError: Raised when ``env_overrides`` is not a string-keyed object.
        """

        super().clean()
        overrides = self.env_overrides or {}
        if not isinstance(overrides, dict):
            raise ValidationError({"env_overrides": "Provide environment overrides as an object."})

        normalized: dict[str, str] = {}
        errors: list[str] = []
        for key, value in overrides.items():
            normalized_key = str(key).strip()
            if not _ENV_KEY_RE.match(normalized_key):
                errors.append(f"Invalid environment key: {normalized_key!r}.")
                continue
            normalized[normalized_key] = "" if value is None else str(value)

        if errors:
            raise ValidationError({"env_overrides": errors})

        self.env_overrides = normalized
        self.is_active = False
        self.is_runnable = False

    def save(self, *args, **kwargs):
        """Persist the prototype metadata after validation.

        Parameters:
            *args: Positional arguments forwarded to Django's model ``save``.
            **kwargs: Keyword arguments forwarded to Django's model ``save``.

        Returns:
            Prototype: The saved model instance.

        Raised exceptions:
            ValidationError: Raised when model validation fails.
        """

        self.full_clean()
        return super().save(*args, **kwargs)
