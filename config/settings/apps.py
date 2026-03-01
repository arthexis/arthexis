"""Application registration and site wiring settings."""

from django.contrib.sites import shortcuts as sites_shortcuts
from django.contrib.sites.requests import RequestSite

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


LOCAL_APPS = [
    "apps.base",
    "apps.blog",
    "apps.credentials",
    "apps.actions",
    "apps.celery",
    "apps.nodes",
    "apps.discovery",
    "apps.ftp",
    "apps.dns",
    "apps.screens",
    "apps.sensors",
    "apps.pyxel",
    "apps.counters",
    "apps.energy",
    "apps.groups",
    "apps.core",
    "apps.graphql",
    "apps.mcp",
    "apps.users",
    "apps.leads",
    "apps.embeds",
    "apps.flows",
    "apps.release",
    "apps.emails",
    "apps.extensions",
    "apps.desktop",
    "apps.payments",
    "apps.sponsors",
    "apps.links",
    "apps.docs",
    "apps.gdrive",
    "apps.calendars",
    "apps.maps",
    "apps.locals",
    "apps.locale",
    "apps.content",
    "apps.clocks",
    "apps.audio",
    "apps.bluetooth",
    "apps.video",
    "apps.media",
    "apps.mermaid",
    "apps.odoo",
    "apps.sigils",
    "apps.selenium",
    "apps.repos",
    "apps.reports",
    "apps.app",
    "apps.rates",
    "apps.vehicle",
    "apps.protocols",
    "apps.ocpp",
    "apps.ocpp.forwarder",
    "apps.simulators",
    "apps.meta",
    "apps.awg",
    "apps.chats",
    "apps.aws",
    "apps.alexa",
    "apps.socials",
    "apps.ops",
    "apps.survey",
    "apps.modules",
    "apps.features",
    "apps.prompts",
    "apps.widgets",
    "apps.apis",
    "apps.sites",
    "apps.smb",
    "apps.summary",
    "apps.services",
    "apps.certs",
    "apps.nginx",
    "apps.cards",
    "apps.nfts",
    "apps.tasks",
    "apps.recipes",
    "apps.tests",
    "apps.teams",
    "apps.logbook",
    "apps.nmcli",
    "apps.wikis",
    "apps.totp",
    "apps.terms",
    "apps.evergo",
    "apps.fitbit",
]

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
    """Fallback to RequestSite when DB-backed Site rows are unavailable."""
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
