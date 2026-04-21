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

from utils.env import env_bool

from config.admin_urls import normalize_admin_url_path
from config.settings_helpers import load_secret_key

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
REPORTS_HTML_TO_PDF_ENABLED = env_bool("REPORTS_HTML_TO_PDF_ENABLED", True)
ROUTE_PROVIDERS = [
    "apps.actions.routes",
    "apps.awg.routes",
    "apps.cards.routes",
    "apps.certs.routes",
    "apps.clocks.routes",
    "apps.core.routes",
    "apps.docs.routes",
    "apps.embeds.routes",
    "apps.evergo.routes",
    "apps.features.routes",
    "apps.gallery.routes",
    "apps.links.routes",
    "apps.logbook.routes",
    "apps.meta.routes",
    "apps.netmesh.routes",
    "apps.nodes.routes",
    "apps.ocpp.routes",
    "apps.odoo.routes",
    "apps.ops.routes",
    "apps.repos.routes",
    "apps.rpiconnect.routes",
    "apps.shop.routes",
    "apps.sites.routes",
    "apps.souls.routes",
    "apps.survey.routes",
    "apps.tasks.routes",
    "apps.teams.routes",
    "apps.terms.routes",
    "apps.video.routes",
    "apps.widgets.routes",
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
                "apps.links.context_processors.share_short_url",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Email settings
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "noreply@example.com")
SERVER_EMAIL = DEFAULT_FROM_EMAIL

# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Domain-specific settings modules
from .channels import *  # noqa: F401,F403
from .video import *  # noqa: F401,F403
from .auth import *  # noqa: F401,F403
from .chat import *  # noqa: F401,F403
from .integrations import *  # noqa: F401,F403
