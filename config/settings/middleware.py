"""Middleware stack settings."""

from django.http import HttpRequest

from .base import HAS_DEBUG_TOOLBAR

MIDDLEWARE = [
    "config.middleware.CrossOriginOpenerPolicyMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "config.middleware.ActiveAppMiddleware",
    "config.middleware.SiteHttpsRedirectMiddleware",
    "config.middleware.ContentSecurityPolicyMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "apps.sites.middleware.LanguagePreferenceMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.ops.middleware.ActiveOperationMiddleware",
    "config.middleware.UsageAnalyticsMiddleware",
    "apps.sigils.middleware.SigilContextMiddleware",
    "apps.sites.middleware.ViewHistoryMiddleware",
    "config.middleware.PageMissLoggingMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ANALYTICS_EXCLUDED_URL_PREFIXES = ("/__debug__", "/healthz", "/status")

if HAS_DEBUG_TOOLBAR:
    MIDDLEWARE.insert(0, "debug_toolbar.middleware.DebugToolbarMiddleware")
    INTERNAL_IPS = ["127.0.0.1", "localhost", "0.0.0.0"]

    def _show_toolbar(_: HttpRequest) -> bool:
        """Always show the toolbar when running in DEBUG mode."""
        return True

    DEBUG_TOOLBAR_CONFIG = {"SHOW_TOOLBAR_CALLBACK": _show_toolbar}

CSRF_FAILURE_VIEW = "apps.sites.views.csrf_failure"
X_FRAME_OPTIONS = "SAMEORIGIN"
