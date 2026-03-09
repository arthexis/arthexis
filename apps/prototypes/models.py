"""Models for local prototype environments and scaffolds."""

from __future__ import annotations

from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured
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
        help_text=(
            "Installed module path for this prototype. Leave blank to scaffold a hidden "
            "app under apps._prototypes."
        ),
    )
    app_label = models.CharField(
        max_length=100,
        unique=True,
        blank=True,
        help_text=(
            "Django app label used when the prototype app is active. Existing apps default "
            "to their AppConfig label."
        ),
    )
    port = models.PositiveIntegerField(
        default=8890,
        help_text="Backend port written to .locks/backend_port.lck when active.",
    )
    sqlite_path = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional SQLite path override for this prototype.",
    )
    sqlite_test_path = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional SQLite test database override for this prototype.",
    )
    cache_dir = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional cache directory override written to DJANGO_CACHE_DIR.",
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
    def default_app_module(self) -> str:
        return f"{self.PROTOTYPE_PACKAGE_ROOT}.{self.slug}"

    @property
    def default_app_label(self) -> str:
        return f"prototype_{self.slug}"

    @property
    def scaffold_module(self) -> str:
        return self.app_module or self.default_app_module

    @property
    def scaffold_label(self) -> str:
        return self.app_label or self.default_app_label

    @property
    def uses_hidden_scaffold(self) -> bool:
        return self.scaffold_module == self.default_app_module

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

    def resolved_sqlite_path(
        self,
        *,
        base_dir: Path | None = None,
        default_to_isolated: bool = False,
    ) -> Path | None:
        if self.sqlite_path:
            return self.resolve_path(self.sqlite_path, base_dir=base_dir)
        if default_to_isolated:
            return self.resolve_path(self.default_sqlite_path(), base_dir=base_dir)
        return None

    def resolved_sqlite_test_path(
        self,
        *,
        base_dir: Path | None = None,
        default_to_isolated: bool = False,
    ) -> Path | None:
        if self.sqlite_test_path:
            return self.resolve_path(self.sqlite_test_path, base_dir=base_dir)
        if default_to_isolated:
            return self.resolve_path(self.default_sqlite_test_path(), base_dir=base_dir)
        return None

    def resolved_cache_dir(
        self,
        *,
        base_dir: Path | None = None,
        default_to_isolated: bool = False,
    ) -> Path | None:
        if self.cache_dir:
            return self.resolve_path(self.cache_dir, base_dir=base_dir)
        if default_to_isolated:
            return self.resolve_path(self.default_cache_dir(), base_dir=base_dir)
        return None

    @staticmethod
    def _resolve_existing_app_label(module_name: str) -> str:
        try:
            return AppConfig.create(module_name).label
        except (ImproperlyConfigured, ImportError, ModuleNotFoundError, ValueError) as exc:
            raise ValidationError(
                {"app_module": f"Installed app module could not be loaded: {module_name}"}
            ) from exc

    def clean(self) -> None:
        """Normalize derived fields and validate custom env overrides."""

        super().clean()
        if self.slug and not _SLUG_RE.match(self.slug):
            raise ValidationError({"slug": "Use lowercase snake_case starting with a letter."})

        self.app_module = (self.app_module or "").strip()
        self.app_label = (self.app_label or "").strip()
        self.sqlite_path = (self.sqlite_path or "").strip()
        self.sqlite_test_path = (self.sqlite_test_path or "").strip()
        self.cache_dir = (self.cache_dir or "").strip()

        if not self.app_module:
            self.app_module = self.default_app_module

        if self.uses_hidden_scaffold:
            if not self.app_label:
                self.app_label = self.default_app_label
        else:
            resolved_label = self._resolve_existing_app_label(self.app_module)
            if self.app_label and self.app_label != resolved_label:
                raise ValidationError(
                    {
                        "app_label": (
                            f"Installed app {self.app_module} uses label {resolved_label!r}; "
                            "leave app_label blank or match that label."
                        )
                    }
                )
            self.app_label = resolved_label

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
