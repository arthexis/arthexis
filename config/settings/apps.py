"""Application registry and site integration settings."""

from pathlib import Path

from django.contrib.sites import shortcuts as sites_shortcuts
from django.contrib.sites.requests import RequestSite

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


def _is_django_app_dir(path: Path) -> bool:
    """Return whether the given directory looks like a conventional Django app package."""

    if not path.is_dir() or path.name.startswith(".") or path.name.startswith("_"):
        return False

    return (path / "__init__.py").exists() and (path / "apps.py").exists()


def _load_local_apps() -> list[str]:
    """Load local Django apps from ``apps/*`` using standard package discovery."""

    return [
        f"apps.{app_dir.name}"
        for app_dir in sorted(APPS_DIR.iterdir(), key=lambda candidate: candidate.name)
        if _is_django_app_dir(app_dir)
    ]


LOCAL_APPS = _load_local_apps()

INSTALLED_APPS = [
    "whitenoise.runserver_nostatic",
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
] + LOCAL_APPS

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
