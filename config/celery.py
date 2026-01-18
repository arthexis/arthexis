"""Celery application configuration."""

import os

from celery import Celery


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from django.conf import settings  # noqa: E402

# When running on production-oriented nodes, avoid Celery debug mode.
node_role = str(getattr(settings, "NODE_ROLE", "")).strip().lower()
production_roles = {
    "watchtower",
    "constellation",
    "satellite",
    "control",
    "terminal",
    "gateway",
}
if node_role in production_roles:
    for var in ["CELERY_TRACE_APP", "CELERY_DEBUG"]:
        os.environ.pop(var, None)
    os.environ.setdefault("CELERY_LOG_LEVEL", "INFO")

# Ensure a durable broker is used on production roles even if redis.env is not loaded.
if node_role in production_roles and not os.environ.get("CELERY_BROKER_URL"):
    os.environ["CELERY_BROKER_URL"] = "redis://localhost:6379/0"
if node_role in production_roles and not os.environ.get("CELERY_RESULT_BACKEND"):
    os.environ["CELERY_RESULT_BACKEND"] = "redis://localhost:6379/0"

app = Celery("config")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@app.task(bind=True)
def debug_task(self):  # pragma: no cover - debug helper
    """A simple debug task."""
    print(f"Request: {self.request!r}")
