"""Application registry and site integration settings."""

from django.contrib.sites import shortcuts as sites_shortcuts
from django.contrib.sites.requests import RequestSite
from django.core.exceptions import ImproperlyConfigured

from .base import APPS_DIR, BASE_DIR, HAS_DEBUG_TOOLBAR


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


def _iter_local_app_package_paths() -> list[str]:
    """Return sorted local app package paths discovered by ``apps.py`` modules."""

    app_packages: list[str] = []
    for app_config_module in sorted(APPS_DIR.rglob("apps.py")):
        package_path = app_config_module.parent.relative_to(BASE_DIR)
        app_packages.append(".".join(package_path.parts))

    return app_packages


def _validate_local_app_entry(app_entry: str) -> None:
    """Ensure a local app entry can be resolved as a Django app module/config."""

    from django.apps import AppConfig

    try:
        AppConfig.create(app_entry)
    except (ImportError, ImproperlyConfigured, ModuleNotFoundError) as exc:
        raise ImproperlyConfigured(
            f"Manifest app entry '{app_entry}' is not importable."
        ) from exc


def _load_local_apps() -> list[str]:
    """Collect Django app package entries discovered from local ``apps.py`` modules."""

    app_entries = _iter_local_app_package_paths()
    return _dedupe_app_entries(app_entries)


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
