"""Static and media asset settings."""

from config.whitenoise import add_headers as whitenoise_add_headers

from .base import BASE_DIR, DEBUG

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "static"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
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
# Some CI and freshly-upgraded environments may render templates before
# collectstatic has been executed for newly-added assets. Fall back to the
# unhashed path instead of raising ValueError for missing manifest entries.
WHITENOISE_MANIFEST_STRICT = False
WHITENOISE_AUTOREFRESH = DEBUG

MERMAID_USE_CDN = True
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
