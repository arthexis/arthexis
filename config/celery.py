"""Celery application configuration."""

import os

from celery import Celery


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# When running on production-oriented nodes, avoid Celery debug mode.
NODE_ROLE = os.environ.get("NODE_ROLE", "")
PRODUCTION_ROLES = {
    "watchtower",
    "constellation",
    "satellite",
    "control",
    "terminal",
    "gateway",
}
if NODE_ROLE.lower() in PRODUCTION_ROLES:
    for var in ["CELERY_TRACE_APP", "CELERY_DEBUG"]:
        os.environ.pop(var, None)
    os.environ.setdefault("CELERY_LOG_LEVEL", "INFO")

app = Celery("config")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@app.task(bind=True)
def debug_task(self):  # pragma: no cover - debug helper
    """A simple debug task."""
    print(f"Request: {self.request!r}")
