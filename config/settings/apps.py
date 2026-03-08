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


def _is_private_package_path(path: Path) -> bool:
    """Return whether any segment in the path is private/hidden."""

    return any(part.startswith((".", "_")) for part in path.parts)


def _is_django_app_dir(path: Path) -> bool:
    """Return whether the given directory looks like a conventional Django app package."""

    if not path.is_dir():
        return False

    relative_path = path.relative_to(APPS_DIR)
    if _is_private_package_path(relative_path):
        return False

    if not (path / "__init__.py").exists():
        return False

    if path.parent == APPS_DIR and (APPS_DIR / "publish" / path.name).exists():
        return False

    if (path / "apps.py").exists():
        return True

    if len(relative_path.parts) != 1:
        return False

    return any(
        (path / marker).exists()
        for marker in ("models.py", "admin.py", "migrations", "templates", "static")
    )


def _to_module_path(path: Path) -> str:
    """Convert an app directory into its importable ``apps.*`` module path."""

    return f"apps.{'.'.join(path.relative_to(APPS_DIR).parts)}"


def _load_local_apps() -> list[str]:
    """Load local Django apps from ``apps/`` using package discovery."""

    app_dirs = [
        candidate.parent
        for candidate in APPS_DIR.rglob("__init__.py")
        if _is_django_app_dir(candidate.parent)
    ]

    return sorted(_to_module_path(app_dir) for app_dir in app_dirs)


LOCAL_APPS = _load_local_apps()

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
