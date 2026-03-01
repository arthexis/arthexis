"""Static and media asset settings."""

from config.whitenoise import add_headers as whitenoise_add_headers

from .base import BASE_DIR, DEBUG

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "static"
STATICFILES_BACKEND = (
    "django.contrib.staticfiles.storage.StaticFilesStorage"
    if DEBUG
    else "whitenoise.storage.CompressedManifestStaticFilesStorage"
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
