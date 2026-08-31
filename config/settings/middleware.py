"""Middleware stack configuration."""

from collections.abc import Iterable, Sequence

from django.http import HttpRequest

from .apps import INSTALLED_APPS, _app_entry_aliases
from .base import HAS_DEBUG_TOOLBAR

MiddlewareEntry = str | tuple[str, str]

_MIDDLEWARE_ENTRIES: tuple[MiddlewareEntry, ...] = (
    # Must be first to run last in the response phase to strip COOP headers.
    "config.middleware.CrossOriginOpenerPolicyMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "config.middleware.ActiveAppMiddleware",
    "config.middleware.SiteHttpsRedirectMiddleware",
    "config.middleware.ContentSecurityPolicyMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    ("apps.sites", "apps.sites.middleware.SharePreviewPublicMiddleware"),
    ("apps.ops", "apps.ops.middleware.ActiveOperationMiddleware"),
    "config.middleware.UsageAnalyticsMiddleware",
    ("apps.sigils", "apps.sigils.middleware.SigilContextMiddleware"),
    ("apps.sites", "apps.sites.middleware.ViewHistoryMiddleware"),
    "config.middleware.PageMissLoggingMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
)


def _installed_app_aliases(installed_apps: Iterable[str]) -> set[str]:
    aliases: set[str] = set()
    for app_entry in installed_apps:
        aliases.update(_app_entry_aliases(app_entry))
    return aliases


def _resolve_middleware_entries(
    entries: Sequence[MiddlewareEntry] = _MIDDLEWARE_ENTRIES,
    *,
    installed_apps: Iterable[str] = INSTALLED_APPS,
) -> list[str]:
    installed_aliases = _installed_app_aliases(installed_apps)
    middleware: list[str] = []
    for entry in entries:
        if isinstance(entry, str):
            middleware.append(entry)
            continue

        app_selector, middleware_path = entry
        if app_selector in installed_aliases:
            middleware.append(middleware_path)

    return middleware


MIDDLEWARE = _resolve_middleware_entries()

ANALYTICS_EXCLUDED_URL_PREFIXES = ("/__debug__", "/healthz", "/status")

if HAS_DEBUG_TOOLBAR:
    MIDDLEWARE.insert(0, "debug_toolbar.middleware.DebugToolbarMiddleware")
    INTERNAL_IPS = ["127.0.0.1", "localhost", "0.0.0.0"]

    def _show_toolbar(_: HttpRequest) -> bool:
        """Always show the toolbar when DEBUG is enabled."""

        return True

    DEBUG_TOOLBAR_CONFIG = {"SHOW_TOOLBAR_CALLBACK": _show_toolbar}
