"""Core settings shared across the project."""

import base64
import contextlib
import hashlib
import importlib.util
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from config.admin_urls import normalize_admin_url_path
from config.settings_helpers import load_secret_key
from utils.env import env_bool, env_int

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = _PROJECT_ROOT
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
WORKGROUP_JOURNAL_PATH = os.environ.get(
    "ARTHEXIS_WORKGROUP_JOURNAL_PATH",
    str(_PROJECT_ROOT.parent / "workgroup.txt"),
)
JOURNAL_FILES = [
    {
        "slug": "workgroup",
        "title": "Workgroup",
        "path": WORKGROUP_JOURNAL_PATH,
        "description": "Shared workgroup coordination journal.",
        "required_field": "Name",
        "field_names": [
            "Name",
            "Current task",
            "Cover",
            "Status",
            "Scope",
            "Date",
        ],
    }
]

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
NODES_ENABLE_SIBLING_IPC = env_bool("NODES_ENABLE_SIBLING_IPC", False)
ENABLE_USAGE_ANALYTICS = env_bool("ENABLE_USAGE_ANALYTICS", False)
RFID_WATCHLISTS_ENABLED = env_bool("RFID_WATCHLISTS_ENABLED", True)
REPORTS_HTML_TO_PDF_ENABLED = env_bool("REPORTS_HTML_TO_PDF_ENABLED", True)
DESKTOP_UI_ENABLED = env_bool("DESKTOP_UI_ENABLED", env_bool("DESKTOP_UI", False))
# Legacy alias used by terminal-launching deployments.
DESKTOP_UI = DESKTOP_UI_ENABLED
_repository_assignment_upstream_url = os.environ.get(
    "ARTHEXIS_REPOSITORY_ASSIGNMENT_UPSTREAM_URL", ""
).strip()
REPOSITORY_WORK_ASSIGNMENT_UPSTREAM_URL = os.environ.get(
    "REPOSITORY_WORK_ASSIGNMENT_UPSTREAM_URL",
    _repository_assignment_upstream_url,
).strip()
REPOSITORY_ASSIGNMENT_UPSTREAM_URL = os.environ.get(
    "REPOSITORY_ASSIGNMENT_UPSTREAM_URL",
    REPOSITORY_WORK_ASSIGNMENT_UPSTREAM_URL,
).strip()
_repository_assignment_sync_token = os.environ.get(
    "ARTHEXIS_REPOSITORY_ASSIGNMENT_SYNC_TOKEN", ""
).strip()
REPOSITORY_WORK_ASSIGNMENT_SYNC_TOKEN = os.environ.get(
    "REPOSITORY_WORK_ASSIGNMENT_SYNC_TOKEN",
    _repository_assignment_sync_token,
).strip()
REPOSITORY_ASSIGNMENT_SYNC_TOKEN = os.environ.get(
    "REPOSITORY_ASSIGNMENT_SYNC_TOKEN",
    REPOSITORY_WORK_ASSIGNMENT_SYNC_TOKEN,
).strip()
REPOSITORY_WORK_ASSIGNMENT_SYNC_TIMEOUT_SECONDS = env_int(
    "REPOSITORY_WORK_ASSIGNMENT_SYNC_TIMEOUT_SECONDS",
    10,
)
REPOSITORY_ASSIGNMENT_SYNC_TIMEOUT_SECONDS = env_int(
    "REPOSITORY_ASSIGNMENT_SYNC_TIMEOUT_SECONDS",
    REPOSITORY_WORK_ASSIGNMENT_SYNC_TIMEOUT_SECONDS,
)
THERMOMETER_SOURCE = os.environ.get("THERMOMETER_SOURCE", "auto").strip() or "auto"
THERMOMETER_PATH_TEMPLATE = (
    os.environ.get(
        "THERMOMETER_PATH_TEMPLATE",
        "/sys/bus/w1/devices/{slug}/temperature",
    ).strip()
    or "/sys/bus/w1/devices/{slug}/temperature"
)
THERMOMETER_I2C_PATH_TEMPLATE = os.environ.get(
    "THERMOMETER_I2C_PATH_TEMPLATE", ""
).strip()
ROUTE_PROVIDERS = [
    "apps.actions.routes",
    "apps.cards.routes",
    "apps.certs.routes",
    "apps.clocks.routes",
    "apps.core.routes",
    "apps.features.routes",
    "apps.imager.routes",
    "apps.nodes.routes",
    "apps.ocpp.routes",
    "apps.odoo.routes",
    "apps.ops.routes",
    "apps.repos.routes",
    "apps.rpiconnect.routes",
    "apps.sites.routes",
    "apps.skills.routes",
]
ASGI_ROUTE_PROVIDERS = [
    "apps.sites.routing",
    "apps.nodes.routing",
    "apps.ocpp.routing",
]
ADMIN_URL_PATH = normalize_admin_url_path(os.environ.get("ADMIN_URL_PATH", "admin/"))
ADMIN_SITE_HEADER = os.environ.get("ADMIN_SITE_HEADER", "Constellation")
ADMIN_SITE_TITLE = os.environ.get("ADMIN_SITE_TITLE", "Constellation")
ADMIN_INDEX_TITLE = os.environ.get("ADMIN_INDEX_TITLE", "Site administration")

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
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Email settings
EMAIL_BACKEND = (
    os.environ.get(
        "EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend"
    ).strip()
    or "django.core.mail.backends.smtp.EmailBackend"
)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "localhost").strip() or "localhost"
EMAIL_PORT = env_int("EMAIL_PORT", 25, min_value=1, max_value=65535)
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "").strip()
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", False)
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)
DEFAULT_ADMIN_EMAIL = os.environ.get("DEFAULT_ADMIN_EMAIL", "").strip()
DEFAULT_ADMIN_USERNAME = (
    os.environ.get("DEFAULT_ADMIN_USERNAME", "arthexis").strip() or "arthexis"
)
DEFAULT_FROM_EMAIL = os.environ.get(
    "DEFAULT_FROM_EMAIL", DEFAULT_ADMIN_EMAIL or "noreply@example.com"
).strip()
SERVER_EMAIL = os.environ.get("SERVER_EMAIL", DEFAULT_FROM_EMAIL).strip()
ADMINS = [("Arthexis Admin", DEFAULT_ADMIN_EMAIL)] if DEFAULT_ADMIN_EMAIL else []

# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Domain-specific settings modules
from .auth import *  # noqa: F401,F403
from .channels import *  # noqa: F401,F403
from .chat import *  # noqa: F401,F403
from .integrations import *  # noqa: F401,F403
