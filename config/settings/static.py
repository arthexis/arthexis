"""Static/media asset and related delivery settings."""

import os

from config.whitenoise import add_headers as whitenoise_add_headers

from .base import BASE_DIR, DEBUG

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "static"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"
WHITENOISE_ADD_HEADERS_FUNCTION = whitenoise_add_headers

ADMIN_BASE_STYLESHEET = "core/admin_ui_framework.css"
ADMIN_GLOBAL_STYLESHEETS = ("sites/css/admin/base_site.css",)
ADMIN_APP_STYLESHEETS: dict[str, str] = {}

WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = DEBUG

MERMAID_USE_CDN = True
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
DEFAULT_FROM_EMAIL = "arthexis@gmail.com"
SERVER_EMAIL = DEFAULT_FROM_EMAIL

SLACK_CLIENT_ID = os.environ.get("SLACK_CLIENT_ID", "")
SLACK_CLIENT_SECRET = os.environ.get("SLACK_CLIENT_SECRET", "")
SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET", "")
SLACK_BOT_SCOPES = os.environ.get(
    "SLACK_BOT_SCOPES",
    "commands,chat:write,chat:write.public",
)
SLACK_REDIRECT_URL = os.environ.get("SLACK_REDIRECT_URL", "")
