"""Early pytest bootstrap that must run before plugin registration."""

from __future__ import annotations

import os
from pathlib import Path

import django
from django.conf import settings

from tests.plugins.sqlite_paths import configure_ephemeral_sqlite_paths, ensure_clean_test_databases


class DisableMigrations(dict):
    """Short-circuit Django migrations for faster test database setup."""

    def __contains__(self, item: object) -> bool:  # pragma: no cover - trivial
        return True

    def __getitem__(self, item: str) -> None:  # pragma: no cover - trivial
        return None


def apply_bootstrap(base_dir: Path) -> None:
    """Apply environment and Django bootstrap required before pytest plugin loading."""

    os.environ.setdefault("ARTHEXIS_DB_BACKEND", "sqlite")
    os.environ.setdefault("PYTEST_DISABLE_MIGRATIONS", "1")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    configure_ephemeral_sqlite_paths()
    ensure_clean_test_databases(base_dir)
    django.setup()

    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }

    if os.environ.get("PYTEST_DISABLE_MIGRATIONS", "0") == "1":
        settings.MIGRATION_MODULES = DisableMigrations()
