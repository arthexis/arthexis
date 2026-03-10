"""Models for local prototype environments and scaffolds."""

from __future__ import annotations

import re
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models

from apps.base.models import Entity


_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class Prototype(Entity):
    """Describe a local prototype and the environment it should activate."""

    PROTOTYPE_PACKAGE_ROOT = "apps._prototypes"
    STATE_ROOT = Path(".state") / "prototypes"
    RESERVED_ENV_KEYS = {
        "ARTHEXIS_ACTIVE_PROTOTYPE",
        "ARTHEXIS_PROTOTYPE_APP",
        "ARTHEXIS_SQLITE_PATH",
        "ARTHEXIS_SQLITE_TEST_PATH",
        "DJANGO_CACHE_DIR",
    }

    slug = models.SlugField(
        max_length=80,
        unique=True,
        validators=[
            RegexValidator(
                regex=_SLUG_RE.pattern,
                message="Use lowercase snake_case starting with a letter.",
            )
        ],
        help_text="Stable prototype slug used for folders and app module names.",
    )
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    app_module = models.CharField(
        max_length=255,
        unique=True,
        blank=True,
        help_text="Installed module path for the hidden prototype app.",
    )
    app_label = models.CharField(
        max_length=100,
        unique=True,
        blank=True,
        help_text="Django app label used when the prototype app is active.",
    )
    port = models.PositiveIntegerField(
        default=8890,
        help_text="Backend port written to .locks/backend_port.lck when active.",
    )
    sqlite_path = models.CharField(
        max_length=255,
        blank=True,
        help_text="Relative or absolute SQLite path for this prototype.",
    )
    sqlite_test_path = models.CharField(
        max_length=255,
        blank=True,
        help_text="SQLite test database path for this prototype.",
    )
    cache_dir = models.CharField(
        max_length=255,
        blank=True,
        help_text="Cache directory written to DJANGO_CACHE_DIR for this prototype.",
    )
    env_overrides = models.JSONField(
        default=dict,
        blank=True,
        help_text="Extra environment variables applied only while this prototype is active.",
    )
    is_active = models.BooleanField(default=False, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "slug"]
        verbose_name = "Prototype"
        verbose_name_plural = "Prototypes"

    def __str__(self) -> str:  # pragma: no cover - trivial representation
        return self.name

    @property
    def state_root(self) -> Path:
        return self.STATE_ROOT / self.slug

    @property
    def scaffold_module(self) -> str:
        return self.app_module or f"{self.PROTOTYPE_PACKAGE_ROOT}.{self.slug}"

    @property
    def scaffold_label(self) -> str:
        return self.app_label or f"prototype_{self.slug}"

    def default_sqlite_path(self) -> str:
        return str(self.state_root / "db.sqlite3")

    def default_sqlite_test_path(self) -> str:
        return str(self.state_root / "test_db.sqlite3")

    def default_cache_dir(self) -> str:
        return str(self.state_root / "cache")

    def resolve_path(self, value: str, *, base_dir: Path | None = None) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        from django.conf import settings

        return Path(base_dir or settings.BASE_DIR) / path

    def resolved_sqlite_path(self, *, base_dir: Path | None = None) -> Path:
        return self.resolve_path(
            self.sqlite_path or self.default_sqlite_path(),
            base_dir=base_dir,
        )

    def resolved_sqlite_test_path(self, *, base_dir: Path | None = None) -> Path:
        return self.resolve_path(
            self.sqlite_test_path or self.default_sqlite_test_path(),
            base_dir=base_dir,
        )

    def resolved_cache_dir(self, *, base_dir: Path | None = None) -> Path:
        return self.resolve_path(
            self.cache_dir or self.default_cache_dir(),
            base_dir=base_dir,
        )

    def clean(self) -> None:
        """Normalize derived fields and validate custom env overrides."""

        super().clean()
        if not self.app_module:
            self.app_module = f"{self.PROTOTYPE_PACKAGE_ROOT}.{self.slug}"
        if not self.app_label:
            self.app_label = f"prototype_{self.slug}"
        if not self.sqlite_path:
            self.sqlite_path = self.default_sqlite_path()
        if not self.sqlite_test_path:
            self.sqlite_test_path = self.default_sqlite_test_path()
        if not self.cache_dir:
            self.cache_dir = self.default_cache_dir()

        overrides = self.env_overrides or {}
        if not isinstance(overrides, dict):
            raise ValidationError({"env_overrides": "Provide environment overrides as an object."})

        normalized: dict[str, str] = {}
        errors: dict[str, list[str]] = {}
        for key, value in overrides.items():
            normalized_key = str(key).strip()
            if not _ENV_KEY_RE.match(normalized_key):
                errors.setdefault("env_overrides", []).append(
                    f"Invalid environment key: {normalized_key!r}."
                )
                continue
            if normalized_key in self.RESERVED_ENV_KEYS:
                errors.setdefault("env_overrides", []).append(
                    f"{normalized_key} is managed by prototype activation."
                )
                continue
            normalized[normalized_key] = "" if value is None else str(value)

        if errors:
            raise ValidationError(errors)

        self.env_overrides = normalized

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
