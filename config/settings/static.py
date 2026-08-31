"""Static and media asset settings."""

import sys

from config.whitenoise import add_headers as whitenoise_add_headers

from .base import BASE_DIR, DEBUG

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "static"
_IS_DEV_SERVER = any(command in sys.argv for command in ("runserver", "runserver_plus"))
USE_MANIFEST_STATICFILES = not DEBUG and not _IS_DEV_SERVER
STATICFILES_BACKEND = (
    "whitenoise.storage.CompressedManifestStaticFilesStorage"
    if USE_MANIFEST_STATICFILES
    else "django.contrib.staticfiles.storage.StaticFilesStorage"
)

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": STATICFILES_BACKEND},
}
WHITENOISE_ADD_HEADERS_FUNCTION = whitenoise_add_headers

# Shared admin CSS pipeline: load a framework base first, then global app-shell
# styles, then optional active app stylesheets discovered per admin request.
ADMIN_BASE_STYLESHEET = "core/admin_ui_framework.css"
ADMIN_GLOBAL_STYLESHEETS = (
    "sites/css/admin/base_site.css",
)
ADMIN_APP_STYLESHEETS: dict[str, str] = {}

# Allow development and freshly-updated environments to serve assets which have
# not yet been collected into ``STATIC_ROOT``. Without this setting WhiteNoise
# only looks for files inside ``STATIC_ROOT`` and dashboards like the public
# traffic chart fail to load their JavaScript dependencies.
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = DEBUG


MERMAID_USE_CDN = True
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
