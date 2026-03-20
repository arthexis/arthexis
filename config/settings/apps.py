"""Application registry and site integration settings."""

import importlib.util
import os
from pathlib import Path

from django.contrib.sites import shortcuts as sites_shortcuts
from django.contrib.sites.requests import RequestSite
from django.core.exceptions import ImproperlyConfigured

from .base import APPS_DIR, HAS_DEBUG_TOOLBAR


def _dedupe_app_entries(app_paths: list[str]) -> list[str]:
    """Return app entries with exact duplicates removed while preserving order."""

    deduped: list[str] = []
    seen_entries: set[str] = set()
    for entry in app_paths:
        normalized = entry.strip()
        if normalized in seen_entries:
            continue

        seen_entries.add(normalized)
        deduped.append(normalized)

    return deduped


def _is_private_package_path(path: Path) -> bool:
    """Return whether any segment in the path is private/hidden."""

    return any(part.startswith((".", "_")) for part in path.parts)


def _has_django_app_marker(path: Path, marker: str) -> bool:
    """Return whether *path* contains a discovery marker for a conventional Django app."""

    marker_path = path / marker
    if marker == "migrations":
        return (marker_path / "__init__.py").exists()
    return marker_path.exists()


def _is_django_app_dir(path: Path) -> bool:
    """Return whether the given directory looks like a conventional Django app package."""

    if not path.is_dir():
        return False

    relative_path = path.relative_to(APPS_DIR)
    if _is_private_package_path(relative_path):
        return False

    if not (path / "__init__.py").exists():
        return False

    module_path = _to_module_path(path)
    if module_path in NON_DJANGO_UTILITY_PACKAGES:
        return False

    if (path / "apps.py").exists():
        return True

    if len(relative_path.parts) != 1:
        return False

    return any(
        _has_django_app_marker(path, marker)
        for marker in ("models.py", "admin.py", "migrations", "templates", "static")
    )


def _to_module_path(path: Path) -> str:
    """Convert an app directory into its importable ``apps.*`` module path."""

    return f"apps.{'.'.join(path.relative_to(APPS_DIR).parts)}"


LEGACY_MIGRATION_APPS = [
    "apps._legacy.fitbit_migration_only.apps.FitbitMigrationOnlyConfig",
    "apps.selenium",
]
NON_DJANGO_UTILITY_PACKAGES = {
    "apps.camera",
    "apps.loggers",
}


def _load_local_apps() -> list[str]:
    """Load local Django apps from ``apps/`` using package discovery."""

    app_dirs = [
        candidate.parent
        for candidate in APPS_DIR.rglob("__init__.py")
        if _is_django_app_dir(candidate.parent)
    ]

    return sorted(
        module_path
        for app_dir in app_dirs
        if (module_path := _to_module_path(app_dir)) not in NON_DJANGO_UTILITY_PACKAGES
    )


def _load_active_prototype_app() -> list[str]:
    """Return the explicitly activated hidden prototype app when configured."""

    module_name = os.environ.get("ARTHEXIS_PROTOTYPE_APP", "").strip()
    if not module_name:
        return []

    if importlib.util.find_spec(module_name) is None:
        raise ImproperlyConfigured(
            f"Configured ARTHEXIS_PROTOTYPE_APP could not be imported: {module_name}"
        )

    return [module_name]


LOCAL_APPS = _load_local_apps() + _load_active_prototype_app()

INSTALLED_APPS = [
    "apps.whitenoise",
    "django.contrib.admin",
    "django.contrib.admindocs",
    "config.auth_app.AuthConfig",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django_otp",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_mermaid.apps.MermaidConfig",
    "parler",
    "import_export",
    "django_object_actions",
    "django.contrib.sites",
    "channels",
    "graphene_django",
    "apps.celery.beat_app.CeleryBeatConfig",
] + LOCAL_APPS + LEGACY_MIGRATION_APPS

if HAS_DEBUG_TOOLBAR:
    INSTALLED_APPS.append("debug_toolbar")

INSTALLED_APPS = _dedupe_app_entries(INSTALLED_APPS)

SITE_ID = 1

MIGRATION_MODULES = {
    "sites": "apps.core.sites_migrations",
    # Pin django_celery_beat migrations to a local copy so we can override
    # upstream changes that introduce optional dependencies (e.g. Google
    # Calendar profile) and avoid InvalidBases errors during migrate.
    "django_celery_beat": "apps.celery.beat_migrations",
}

_original_get_current_site = sites_shortcuts.get_current_site


def _get_current_site_with_request_fallback(request=None):
    """Fallback to RequestSite during startup when Site records are unavailable."""

    from django.contrib.sites.models import Site
    from django.db.utils import OperationalError, ProgrammingError

    try:
        return _original_get_current_site(request)
    except (Site.DoesNotExist, OperationalError, ProgrammingError):
        if request is not None:
            return RequestSite(request)
        raise


sites_shortcuts.get_current_site = _get_current_site_with_request_fallback
