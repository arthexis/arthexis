"""Settings for the small, clone-local Arthexis 2.0 foundation."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ARTHEXIS_DATA_DIR", BASE_DIR / "var"))
DATABASE_PATH = Path(os.environ.get("ARTHEXIS_DATABASE_PATH", DATA_DIR / "db.sqlite3"))

SECRET_KEY = os.environ.get(
    "ARTHEXIS_SECRET_KEY", "development-only-change-before-deploy"
)
DEBUG = os.environ.get("ARTHEXIS_DEBUG", "0") == "1"
ALLOWED_HOSTS: list[str] = []

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.base",
    "apps.cards",
    "apps.celery",
    "apps.energy",
    "apps.events",
    "apps.nodes",
    "apps.ocpp",
    "apps.sigils",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "arthexis.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "arthexis.wsgi.application"
ASGI_APPLICATION = "arthexis.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATABASE_PATH,
    },
}

AUTH_PASSWORD_VALIDATORS: list[dict[str, str]] = []
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CHANNEL_REDIS_URL = os.environ.get("ARTHEXIS_CHANNEL_REDIS_URL", "")
OCPP_ENROLLMENT_TOKEN_HASH = os.environ.get("ARTHEXIS_OCPP_ENROLLMENT_TOKEN_HASH", "")
CHANNEL_LAYERS = {
    "default": (
        {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {"hosts": [CHANNEL_REDIS_URL]},
        }
        if CHANNEL_REDIS_URL
        else {"BACKEND": "channels.layers.InMemoryChannelLayer"}
    ),
}

CELERY_BROKER_URL = os.environ.get("ARTHEXIS_CELERY_BROKER_URL", "memory://")
CELERY_RESULT_BACKEND = os.environ.get(
    "ARTHEXIS_CELERY_RESULT_BACKEND", "cache+memory://"
)
CELERY_BEAT_SCHEDULE = {
    "ocpp-refresh-stale-connections": {
        "task": "ocpp.maintenance.refresh_stale_connections",
        "schedule": 3600,
    },
}
