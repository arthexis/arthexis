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
    if module_path in NON_DJANGO_UTILITY_PACKAGES | _legacy_runtime_app_packages():
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


def _legacy_runtime_app_packages() -> set[str]:
    """Return retired runtime app packages implied by ``LEGACY_MIGRATION_APPS``.

    Returns:
        A set of legacy runtime package names that should stay out of automatic
        local app discovery because their migrations are preserved through
        ``LEGACY_MIGRATION_APPS`` shims instead.
    """

    retired_packages: set[str] = set()
    for app_path in LEGACY_MIGRATION_APPS:
        legacy_marker = "apps._legacy."
        migration_suffix = "_migration_only.apps."
        if legacy_marker not in app_path or migration_suffix not in app_path:
            continue

        legacy_name = app_path.split(legacy_marker, maxsplit=1)[1].split(
            migration_suffix, maxsplit=1
        )[0]
        runtime_package = f"apps.{legacy_name.removesuffix('_migration_only')}"
        if runtime_package == "apps.recipes":
            continue
        retired_packages.add(runtime_package)

    return retired_packages


LEGACY_MIGRATION_APPS = [
    "apps._legacy.extensions_migration_only.apps.ExtensionsMigrationOnlyConfig",
    "apps._legacy.prompts_migration_only.apps.PromptsMigrationOnlyConfig",
    "apps._legacy.recipes_migration_only.apps.RecipesMigrationOnlyConfig",
    "apps._legacy.selenium_migration_only.apps.SeleniumMigrationOnlyConfig",
    "apps._legacy.socials_migration_only.apps.SocialsMigrationOnlyConfig",
    "apps._legacy.sponsors_migration_only.apps.SponsorsMigrationOnlyConfig",
    "apps._legacy.survey_migration_only.apps.SurveyMigrationOnlyConfig",
    "config.legacy_mermaid",
]
NON_DJANGO_UTILITY_PACKAGES = {
    "apps.camera",
}


def _load_local_apps() -> list[str]:
    """Load local Django apps from ``apps/`` using package discovery."""

    app_dirs = [
        candidate.parent
        for candidate in APPS_DIR.rglob("__init__.py")
        if _is_django_app_dir(candidate.parent)
    ]

    return sorted(_to_module_path(app_dir) for app_dir in app_dirs)


LOCAL_APPS = _load_local_apps()

INSTALLED_APPS = (
    [
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
        "apps.celery.beat_app.CeleryBeatConfig",
    ]
    + LOCAL_APPS
    + LEGACY_MIGRATION_APPS
)

if HAS_DEBUG_TOOLBAR:
    INSTALLED_APPS.append("debug_toolbar")

INSTALLED_APPS = _dedupe_app_entries(INSTALLED_APPS)

SITE_ID = 1

MIGRATION_MODULES = {
    "selenium": "apps._legacy.selenium_migration_only.migrations",
    "sites": "apps.core.sites_migrations",
    "socials": "apps._legacy.socials_migration_only.migrations",
    "sponsors": "apps._legacy.sponsors_migration_only.migrations",
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
