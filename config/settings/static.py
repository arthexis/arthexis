"""Static and media asset settings."""

from config.whitenoise import add_headers as whitenoise_add_headers

from .base import BASE_DIR, DEBUG

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "static"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"
WHITENOISE_ADD_HEADERS_FUNCTION = whitenoise_add_headers

ADMIN_BASE_STYLESHEET = "core/admin_ui_framework.css"
ADMIN_GLOBAL_STYLESHEETS = (
    "sites/css/admin/base_site.css",
)
ADMIN_APP_STYLESHEETS: dict[str, str] = {}

WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = DEBUG

MERMAID_USE_CDN = True
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
