"""Celery application configuration."""

import os

from celery import Celery


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from django.conf import settings  # noqa: E402

# When running on production-oriented nodes, avoid Celery debug mode.
node_role = str(getattr(settings, "NODE_ROLE", "")).strip().lower()
production_roles = settings.PRODUCTION_ROLES
if node_role in production_roles:
    for var in ["CELERY_TRACE_APP", "CELERY_DEBUG"]:
        os.environ.pop(var, None)
    os.environ.setdefault("CELERY_LOG_LEVEL", "INFO")

    # Ensure a durable broker is used on production roles even if redis.env is not loaded.
    default_prod_broker_url = "redis://localhost:6379/0"
    if not os.environ.get("CELERY_BROKER_URL"):
        os.environ.setdefault("CELERY_BROKER_URL", default_prod_broker_url)
        settings.CELERY_BROKER_URL = default_prod_broker_url
    if not os.environ.get("CELERY_RESULT_BACKEND"):
        os.environ.setdefault("CELERY_RESULT_BACKEND", default_prod_broker_url)
        settings.CELERY_RESULT_BACKEND = default_prod_broker_url

app = Celery("config")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@app.task(bind=True)
def debug_task(self):  # pragma: no cover - debug helper
    """A simple debug task."""
    print(f"Request: {self.request!r}")
