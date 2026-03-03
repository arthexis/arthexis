"""Core settings shared across the project."""

import base64
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path

from config.channel_layer import resolve_channel_layers

import django.utils.encoding as encoding
from utils.env import env_bool

if not hasattr(encoding, "force_text"):  # pragma: no cover - Django>=5 compatibility
    from django.utils.encoding import force_str

    encoding.force_text = force_str

from config.settings_helpers import load_secret_key

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent.parent
APPS_DIR = BASE_DIR / "apps"

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = load_secret_key(BASE_DIR)


def _default_field_encryption_key() -> str:
    """Build a deterministic Fernet-compatible key from SECRET_KEY for local defaults."""
    return base64.urlsafe_b64encode(
        hashlib.sha256(SECRET_KEY.encode("utf-8")).digest()
    ).decode("ascii")


FIELD_ENCRYPTION_KEY = os.environ.get(
    "FIELD_ENCRYPTION_KEY", _default_field_encryption_key()
)
EVERGO_API_LOGIN_URL = os.environ.get(
    "EVERGO_API_LOGIN_URL", "https://portal-backend.evergo.com/api/mex/v1/login"
)
EVERGO_PORTAL_LOGIN_URL = os.environ.get(
    "EVERGO_PORTAL_LOGIN_URL", "https://portal-mex.evergo.com/access/login"
)

# Determine the current node role for role-specific settings while leaving
# DEBUG control to the environment.
NODE_ROLE = os.environ.get("NODE_ROLE")
if NODE_ROLE is None:
    role_lock = BASE_DIR / ".locks" / "role.lck"
    NODE_ROLE = role_lock.read_text().strip() if role_lock.exists() else "Terminal"

PRODUCTION_ROLES = {
    "watchtower",
    "constellation",
    "satellite",
    "control",
    "terminal",
    "gateway",
}

_debugpy_attached = "DEBUGPY_LAUNCHER_PORT" in os.environ
DEBUG = env_bool("DEBUG", _debugpy_attached)
HAS_DEBUG_TOOLBAR = DEBUG and importlib.util.find_spec("debug_toolbar") is not None

# Disable NetMessage propagation when running maintenance commands that should
# avoid contacting remote peers.
NET_MESSAGE_DISABLE_PROPAGATION = env_bool("NET_MESSAGE_DISABLE_PROPAGATION", False)
ENABLE_USAGE_ANALYTICS = env_bool("ENABLE_USAGE_ANALYTICS", False)

CACHE_LOCATION = os.environ.get("DJANGO_CACHE_DIR", str(BASE_DIR / "cache"))
with contextlib.suppress(OSError):
    os.makedirs(CACHE_LOCATION, exist_ok=True)

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": CACHE_LOCATION,
        "TIMEOUT": None,
    }
}

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "apps" / "sites" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.template.context_processors.i18n",
                "django.contrib.messages.context_processors.messages",
                "config.context_processors.site_and_node",
                "apps.sites.context_processors.nav_links",
                "apps.links.context_processors.share_short_url",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Channels configuration
CHANNEL_REDIS_URL = os.environ.get("CHANNEL_REDIS_URL", "").strip()
OCPP_STATE_REDIS_URL = os.environ.get("OCPP_STATE_REDIS_URL", "").strip()
if not OCPP_STATE_REDIS_URL:
    OCPP_STATE_REDIS_URL = (
        CHANNEL_REDIS_URL or os.environ.get("CELERY_BROKER_URL", "").strip()
    )

VIDEO_FRAME_REDIS_URL = os.environ.get("VIDEO_FRAME_REDIS_URL", "").strip()
if not VIDEO_FRAME_REDIS_URL:
    VIDEO_FRAME_REDIS_URL = (
        CHANNEL_REDIS_URL or os.environ.get("CELERY_BROKER_URL", "").strip()
    )
VIDEO_FRAME_CACHE_PREFIX = os.environ.get("VIDEO_FRAME_CACHE_PREFIX", "video:mjpeg")
VIDEO_FRAME_CACHE_TTL = int(os.environ.get("VIDEO_FRAME_CACHE_TTL", "10"))
VIDEO_FRAME_MAX_AGE_SECONDS = int(
    os.environ.get("VIDEO_FRAME_MAX_AGE_SECONDS", "15")
)
VIDEO_FRAME_STREAM_BUFFER_SECONDS = int(
    os.environ.get("VIDEO_FRAME_STREAM_BUFFER_SECONDS", "300")
)
VIDEO_WEBRTC_ICE_SERVERS = []
_webrtc_ice_payload = os.environ.get("VIDEO_WEBRTC_ICE_SERVERS", "").strip()
if _webrtc_ice_payload:
    try:
        parsed = json.loads(_webrtc_ice_payload)
    except (TypeError, ValueError):
        VIDEO_WEBRTC_ICE_SERVERS = []
    else:
        VIDEO_WEBRTC_ICE_SERVERS = parsed if isinstance(parsed, list) else []
VIDEO_FRAME_CAPTURE_INTERVAL = float(
    os.environ.get("VIDEO_FRAME_CAPTURE_INTERVAL", "0.2")
)
VIDEO_FRAME_POLL_INTERVAL = float(os.environ.get("VIDEO_FRAME_POLL_INTERVAL", "0.2"))
VIDEO_FRAME_SERVICE_SLEEP = float(
    os.environ.get("VIDEO_FRAME_SERVICE_SLEEP", "0.05")
)

CHANNEL_LAYERS, CHANNEL_LAYER_DECISION = resolve_channel_layers(
    channel_redis_url=CHANNEL_REDIS_URL,
    ocpp_state_redis_url=OCPP_STATE_REDIS_URL,
)

OCPP_PENDING_CALL_TTL = int(os.environ.get("OCPP_PENDING_CALL_TTL", "1800"))
OCPP_ASYNC_LOGGING = env_bool(
    "OCPP_ASYNC_LOGGING", bool(CHANNEL_REDIS_URL or OCPP_STATE_REDIS_URL)
)
try:
    OCPP_FORWARDER_PING_INTERVAL = int(os.environ.get("OCPP_FORWARDER_PING_INTERVAL", "60"))
except (TypeError, ValueError):
    OCPP_FORWARDER_PING_INTERVAL = 60
if OCPP_FORWARDER_PING_INTERVAL <= 0:
    OCPP_FORWARDER_PING_INTERVAL = 60

PAGES_CHAT_ENABLED = env_bool("PAGES_CHAT_ENABLED", True)
PAGES_CHAT_NOTIFY_STAFF = env_bool("PAGES_CHAT_NOTIFY_STAFF", False)
try:
    PAGES_CHAT_IDLE_ESCALATE_SECONDS = int(
        os.environ.get("PAGES_CHAT_IDLE_ESCALATE_SECONDS", "300")
    )
except (TypeError, ValueError):
    PAGES_CHAT_IDLE_ESCALATE_SECONDS = 300
PAGES_CHAT_SOCKET_PATH = os.environ.get("PAGES_CHAT_SOCKET_PATH", "/ws/pages/chat/")

# Custom user model
AUTH_USER_MODEL = "users.User"

# Enable RFID authentication backend and restrict default admin login to localhost
# Keep LocalhostAdminBackend first so the localhost/IP checks run before password
# or OTP authentication.
AUTHENTICATION_BACKENDS = [
    "apps.users.backends.LocalhostAdminBackend",
    "apps.users.backends.PasswordOrOTPBackend",
    "apps.users.backends.TempPasswordBackend",
    "apps.users.backends.RFIDBackend",
]

# Use the custom login view for all authentication redirects.
LOGIN_URL = "pages:login"

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# Email settings
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "noreply@example.com")
SERVER_EMAIL = DEFAULT_FROM_EMAIL

# Slack bot onboarding
SLACK_CLIENT_ID = os.environ.get("SLACK_CLIENT_ID", "")
SLACK_CLIENT_SECRET = os.environ.get("SLACK_CLIENT_SECRET", "")
SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET", "")
SLACK_BOT_SCOPES = os.environ.get(
    "SLACK_BOT_SCOPES",
    "commands,chat:write,chat:write.public",
)
SLACK_REDIRECT_URL = os.environ.get("SLACK_REDIRECT_URL", "")

# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# GitHub issue reporting
GITHUB_ISSUE_REPORTING_ENABLED = env_bool("GITHUB_ISSUE_REPORTING_ENABLED", True)
GITHUB_ISSUE_REPORTING_COOLDOWN = 3600  # seconds

