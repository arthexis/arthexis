"""Application registry and site integration settings."""

from importlib import import_module

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
    "apps.deploy",
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
    "apps.imager",
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
        if app_path not in allowed_project_apps:
            raise ImproperlyConfigured(
                f"INSTALLED_APPS contains unlisted local app '{app_path}'. "
                "Declare it in PROJECT_LOCAL_APPS or PROJECT_APPS."
            )


_validate_project_local_apps()

SITE_ID = 1

MIGRATION_MODULES = {
    # Pin django_celery_beat migrations to a local copy so we can override
    # upstream changes that introduce optional dependencies (e.g. Google
    # Calendar profile) and avoid InvalidBases errors during migrate.
    "django_celery_beat": "apps.celery.beat_migrations",
    "sites": "apps.core.sites_migrations",
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
