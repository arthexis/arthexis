"""Static and media asset settings."""

from config.whitenoise import add_headers as whitenoise_add_headers

from .base import BASE_DIR, DEBUG, RUNNING_TESTS

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
WHITENOISE_MANIFEST_STRICT = not (DEBUG or RUNNING_TESTS)
WHITENOISE_AUTOREFRESH = DEBUG

# Django's admin static files live in site-packages and are not copied to
# STATIC_ROOT during tests. Manifest storage requires them to exist at hashed
# locations, so use plain finder-based storage for pytest runs.
if RUNNING_TESTS:
    STORAGES["staticfiles"] = {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
    }

MERMAID_USE_CDN = True
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
