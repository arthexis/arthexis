"""Application registry and site integration settings."""

import importlib

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


def _iter_app_manifest_modules() -> list[str]:
    """Return sorted manifest module names discovered under the apps package."""

    manifest_modules: list[str] = []
    for manifest_path in sorted(APPS_DIR.rglob("manifest.py")):
        module_path = manifest_path.relative_to(BASE_DIR).with_suffix("")
        manifest_modules.append(".".join(module_path.parts))

    return manifest_modules


def _validate_manifest_app_entry(app_entry: str) -> None:
    """Ensure a manifest app entry can be resolved as a Django app module/config."""

    from django.apps import AppConfig

    try:
        AppConfig.create(app_entry)
    except (ImportError, ImproperlyConfigured, ModuleNotFoundError) as exc:
        raise ImproperlyConfigured(
            f"Manifest app entry '{app_entry}' is not importable."
        ) from exc


def _load_local_apps_from_manifests() -> list[str]:
    """Collect Django app entries from app manifest modules without importing apps."""

    app_entries: list[str] = []
    for manifest_module in _iter_app_manifest_modules():
        module = importlib.import_module(manifest_module)
        manifest_entries = getattr(module, "DJANGO_APPS", None)

        if manifest_entries is None:
            continue

        if not isinstance(manifest_entries, list):
            raise ImproperlyConfigured(
                f"{manifest_module}.DJANGO_APPS must be defined as a list."
            )

        for manifest_entry in manifest_entries:
            if not isinstance(manifest_entry, str) or not manifest_entry.strip():
                raise ImproperlyConfigured(
                    f"{manifest_module}.DJANGO_APPS contains an invalid entry: {manifest_entry!r}."
                )

            normalized_entry = manifest_entry.strip()
            app_entries.append(normalized_entry)

    return app_entries


LOCAL_APPS = _load_local_apps_from_manifests()

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

INSTALLED_APPS = _dedupe_app_entries(INSTALLED_APPS)

if HAS_DEBUG_TOOLBAR:
    INSTALLED_APPS += ["debug_toolbar"]

SITE_ID = 1

MIGRATION_MODULES = {
    "sites": "apps.core.sites_migrations",
    "django_celery_beat": "apps.celery.beat_migrations",
}

_original_get_current_site = sites_shortcuts.get_current_site


def _get_current_site_with_request_fallback(request=None):
    """Fallback to RequestSite during startup when Site records are unavailable."""

    try:
        return _original_get_current_site(request)
    except Exception as exc:
        from django.contrib.sites.models import Site
        from django.db.utils import OperationalError, ProgrammingError

        recoverable_exceptions = (Site.DoesNotExist, OperationalError, ProgrammingError)

        if request is not None and isinstance(exc, recoverable_exceptions):
            return RequestSite(request)
        raise


sites_shortcuts.get_current_site = _get_current_site_with_request_fallback
