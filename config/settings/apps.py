"""Application registry and site integration settings."""

from importlib import import_module
from importlib.metadata import PackageNotFoundError, version

from django.contrib.sites import shortcuts as sites_shortcuts
from django.contrib.sites.requests import RequestSite
from django.core.exceptions import ImproperlyConfigured

from .base import HAS_DEBUG_TOOLBAR


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


def _read_repo_version() -> str:
    """Return the repository version marker used for migration-line decisions."""

    from .base import BASE_DIR

    version_path = BASE_DIR / "VERSION"
    if not version_path.exists():
        try:
            return version("arthexis")
        except PackageNotFoundError:
            return ""
    return version_path.read_text(encoding="utf-8").strip()


def _parse_major_version(version: str) -> int | None:
    """Return the major version component, or ``None`` when unavailable."""

    head = version.split(".", maxsplit=1)[0].strip()
    if not head.isdigit():
        return None
    return int(head)


def _include_legacy_shims(version: str) -> bool:
    """Keep ``apps._legacy`` shims on 0.x lines; drop them on major upgrades."""

    major = _parse_major_version(version)
    return major is None or major == 0


def _drop_legacy_app_entries(
    app_paths: list[str], legacy_apps: list[str], *, version: str
) -> list[str]:
    """Drop ``apps._legacy`` app configs once the suite switches major lines."""

    if _include_legacy_shims(version):
        return list(app_paths)

    legacy_apps_to_drop = set(legacy_apps)
    return [entry for entry in app_paths if entry not in legacy_apps_to_drop]


def _drop_legacy_migration_modules(
    migration_modules: dict[str, str], *, version: str
) -> dict[str, str]:
    """Drop migration-module shims under ``apps._legacy`` on major upgrades."""

    if _include_legacy_shims(version):
        return dict(migration_modules)
    return {
        app_label: module_path
        for app_label, module_path in migration_modules.items()
        if not module_path.startswith("apps._legacy.")
    }


LEGACY_MIGRATION_APPS = [
    "apps._legacy.calendars_migration_only.apps.CalendarsMigrationOnlyConfig",
    "apps._legacy.extensions_migration_only.apps.ExtensionsMigrationOnlyConfig",
    "apps._legacy.fitbit_migration_only.apps.FitbitMigrationOnlyConfig",
    "apps._legacy.game_migration_only.apps.GameMigrationOnlyConfig",
    "apps._legacy.prompts_migration_only.apps.PromptsMigrationOnlyConfig",
    "apps._legacy.recipes_migration_only.apps.RecipesMigrationOnlyConfig",
    "apps._legacy.screens_migration_only.apps.ScreensMigrationOnlyConfig",
    "apps._legacy.shortcuts_migration_only.apps.ShortcutsMigrationOnlyConfig",
    "apps._legacy.selenium_migration_only.apps.SeleniumMigrationOnlyConfig",
    "apps._legacy.smb_migration_only.apps.SmbMigrationOnlyConfig",
    "apps._legacy.socials_migration_only.apps.SocialsMigrationOnlyConfig",
    "apps._legacy.sponsors_migration_only.apps.SponsorsMigrationOnlyConfig",
    "apps._legacy.survey_migration_only.apps.SurveyMigrationOnlyConfig",
    "config.legacy_mermaid",
]
REPO_VERSION = _read_repo_version()
PROJECT_LOCAL_APPS = [
    "apps.actions",
    "apps.apis",
    "apps.app",
    "apps.audio",
    "apps.awg",
    "apps.aws",
    "apps.base",
    "apps.cards",
    "apps.cdn",
    "apps.celery",
    "apps.certs",
    "apps.chats",
    "apps.classification",
    "apps.clocks",
    "apps.content",
    "apps.core",
    "apps.counters",
    "apps.credentials",
    "apps.desktop",
    "apps.discovery",
    "apps.dns",
    "apps.docs",
    "apps.emails",
    "apps.embeds",
    "apps.energy",
    "apps.evergo",
    "apps.features",
    "apps.flows",
    "apps.forwarder.ocpp",
    "apps.ftp",
    "apps.gdrive",
    "apps.groups",
    "apps.leads",
    "apps.links",
    "apps.locale",
    "apps.locals",
    "apps.logbook",
    "apps.maps",
    "apps.media",
    "apps.meta",
    "apps.modules",
    "apps.nginx",
    "apps.nmcli",
    "apps.nodes",
    "apps.ocpp",
    "apps.odoo",
    "apps.ops",
    "apps.payments",
    "apps.playwright",
    "apps.projects",
    "apps.protocols",
    "apps.prototypes",
    "apps.rates",
    "apps.release",
    "apps.reports",
    "apps.repos",
    "apps.sensors",
    "apps.services",
    "apps.shop",
    "apps.sigils",
    "apps.simulators",
    "apps.sites",
    "apps.special",
    "apps.summary",
    "apps.tasks",
    "apps.teams",
    "apps.terms",
    "apps.tests",
    "apps.totp",
    "apps.users",
    "apps.vehicle",
    "apps.video",
    "apps.widgets",
]
THIRD_PARTY_APPS = [
    "channels",
    "django_mermaid.apps.MermaidConfig",
    "django_object_actions",
    "django_otp",
    "import_export",
    "parler",
]
DJANGO_CORE_APPS = [
    "django.contrib.admin",
    "django.contrib.admindocs",
    "django.contrib.contenttypes",
    "django.contrib.messages",
    "django.contrib.sessions",
    "django.contrib.sites",
    "django.contrib.staticfiles",
]
PROJECT_APPS = [
    "apps.whitenoise",
    "config.auth_app.AuthConfig",
    "apps.celery.beat_app.CeleryBeatConfig",
]

INSTALLED_APPS = (
    PROJECT_APPS
    + DJANGO_CORE_APPS
    + THIRD_PARTY_APPS
    + PROJECT_LOCAL_APPS
    + LEGACY_MIGRATION_APPS
)

INSTALLED_APPS = _drop_legacy_app_entries(
    INSTALLED_APPS,
    LEGACY_MIGRATION_APPS,
    version=REPO_VERSION,
)

if HAS_DEBUG_TOOLBAR:
    INSTALLED_APPS.append("debug_toolbar")

INSTALLED_APPS = _dedupe_app_entries(INSTALLED_APPS)


def _import_base_module(app_path: str) -> None:
    """Import the base module of an app entry."""

    has_app_config_class = app_path.rsplit(".", maxsplit=1)[-1][:1].isupper()
    if ".apps." in app_path:
        base_module = app_path.rsplit(".apps.", maxsplit=1)[0]
    elif has_app_config_class:
        base_module = app_path.rsplit(".", maxsplit=1)[0]
    else:
        base_module = app_path
    import_module(base_module)


def _validate_project_local_apps() -> None:
    """Validate project app wiring is explicit and importable."""

    for app_path in PROJECT_LOCAL_APPS:
        try:
            _import_base_module(app_path)
        except ImportError as exc:
            raise ImproperlyConfigured(
                f"PROJECT_LOCAL_APPS entry '{app_path}' could not be imported."
            ) from exc

    allowed_project_apps = set(PROJECT_LOCAL_APPS) | set(PROJECT_APPS)
    for app_path in INSTALLED_APPS:
        if not app_path.startswith("apps."):
            continue
        if app_path in LEGACY_MIGRATION_APPS:
            continue
        if app_path not in allowed_project_apps:
            raise ImproperlyConfigured(
                f"INSTALLED_APPS contains unlisted local app '{app_path}'. "
                "Declare it in PROJECT_LOCAL_APPS or PROJECT_APPS."
            )


_validate_project_local_apps()

SITE_ID = 1

MIGRATION_MODULES = {
    "calendars": "apps._legacy.calendars_migration_only.migrations",
    "extensions": "apps._legacy.extensions_migration_only.migrations",
    "fitbit": "apps._legacy.fitbit_migration_only.migrations",
    "game": "apps._legacy.game_migration_only.migrations",
    "prompts": "apps._legacy.prompts_migration_only.migrations",
    "recipes": "apps._legacy.recipes_migration_only.migrations",
    "screens": "apps._legacy.screens_migration_only.migrations",
    "selenium": "apps._legacy.selenium_migration_only.migrations",
    "shortcuts": "apps._legacy.shortcuts_migration_only.migrations",
    "sites": "apps.core.sites_migrations",
    "smb": "apps._legacy.smb_migration_only.migrations",
    "socials": "apps._legacy.socials_migration_only.migrations",
    "sponsors": "apps._legacy.sponsors_migration_only.migrations",
    "survey": "apps._legacy.survey_migration_only.migrations",
    # Pin django_celery_beat migrations to a local copy so we can override
    # upstream changes that introduce optional dependencies (e.g. Google
    # Calendar profile) and avoid InvalidBases errors during migrate.
    "django_celery_beat": "apps.celery.beat_migrations",
}
MIGRATION_MODULES = _drop_legacy_migration_modules(MIGRATION_MODULES, version=REPO_VERSION)

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
