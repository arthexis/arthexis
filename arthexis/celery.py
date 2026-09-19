"""Celery application configuration for retained Arthexis tasks."""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "arthexis.settings")

app = Celery("arthexis")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
