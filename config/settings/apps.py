"""Application registry and site integration settings."""

import importlib

from django.contrib.sites import shortcuts as sites_shortcuts
from django.contrib.sites.requests import RequestSite
from django.core.exceptions import ImproperlyConfigured

from utils.enabled_apps_lock import read_enabled_apps_lock

from .base import APPS_DIR, BASE_DIR, HAS_DEBUG_TOOLBAR

REQUIRED_LOCAL_APP_PATHS = ("apps.app", "apps.sites", "apps.users")


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
    manifest_requirements: dict[str, list[str]] = {}
    for manifest_module in _iter_app_manifest_modules():
        module = importlib.import_module(manifest_module)
        manifest_entries = getattr(module, "DJANGO_APPS", None)
        required_entries = getattr(module, "REQUIRES_APPS", [])

        if manifest_entries is None:
            continue

        if not isinstance(manifest_entries, list):
            raise ImproperlyConfigured(
                f"{manifest_module}.DJANGO_APPS must be defined as a list."
            )

        if not isinstance(required_entries, list):
            raise ImproperlyConfigured(
                f"{manifest_module}.REQUIRES_APPS must be defined as a list."
            )

        normalized_requirements: list[str] = []
        for required_entry in required_entries:
            if not isinstance(required_entry, str) or not required_entry.strip():
                raise ImproperlyConfigured(
                    f"{manifest_module}.REQUIRES_APPS contains an invalid entry: {required_entry!r}."
                )

            normalized_requirements.append(required_entry.strip())

        for manifest_entry in manifest_entries:
            if not isinstance(manifest_entry, str) or not manifest_entry.strip():
                raise ImproperlyConfigured(
                    f"{manifest_module}.DJANGO_APPS contains an invalid entry: {manifest_entry!r}."
                )

            normalized_entry = manifest_entry.strip()
            app_entries.append(normalized_entry)

            if normalized_requirements:
                manifest_requirements[normalized_entry] = normalized_requirements

    return _filter_local_apps_by_enabled_lock(
        app_entries,
        requirements_by_app=manifest_requirements,
    )


def _is_required_local_app_entry(app_entry: str) -> bool:
    """Return whether app entry must always remain enabled for core startup."""

    normalized_entry = app_entry.strip()
    return any(
        normalized_entry == required_path
        or normalized_entry.startswith(f"{required_path}.")
        for required_path in REQUIRED_LOCAL_APP_PATHS
    )


def _entry_matches_enabled_selector(app_entry: str, selector: str) -> bool:
    """Return whether a manifest app entry matches a lock-file selector."""

    normalized_entry = app_entry.strip()
    normalized_selector = selector.strip()
    if not normalized_entry or not normalized_selector:
        return False

    if normalized_entry == normalized_selector:
        return True

    label = normalized_entry.rsplit(".", 1)[-1]
    return label == normalized_selector


def _filter_local_apps_by_enabled_lock(
    app_entries: list[str],
    *,
    requirements_by_app: dict[str, list[str]] | None = None,
) -> list[str]:
    """Filter manifest app entries using the optional enabled apps lock file."""

    requirements = requirements_by_app or {}
    for app_entry, required_entries in requirements.items():
        if not isinstance(required_entries, list):
            raise ImproperlyConfigured(
                f"Dependency map for '{app_entry}' must be a list of app entries."
            )
        for required_entry in required_entries:
            if not isinstance(required_entry, str) or not required_entry.strip():
                raise ImproperlyConfigured(
                    f"Dependency map for '{app_entry}' contains an invalid entry: {required_entry!r}."
                )

    def _expand_required_apps(enabled_entries: set[str]) -> set[str]:
        pending = list(enabled_entries)
        expanded = set(enabled_entries)
        while pending:
            current = pending.pop()
            for dependency in requirements.get(current, []):
                if dependency in expanded:
                    continue
                expanded.add(dependency)
                pending.append(dependency)
        return expanded

    selectors = read_enabled_apps_lock(BASE_DIR)
    if selectors is None:
        return [
            app_entry
            for app_entry in app_entries
            if app_entry in _expand_required_apps(set(app_entries))
        ]

    explicitly_enabled = {
        app_entry
        for app_entry in app_entries
        if _is_required_local_app_entry(app_entry)
        or any(
            _entry_matches_enabled_selector(app_entry, selector)
            for selector in selectors
        )
    }

    enabled_with_dependencies = _expand_required_apps(explicitly_enabled)

    return [
        app_entry
        for app_entry in app_entries
        if app_entry in enabled_with_dependencies
    ]


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
