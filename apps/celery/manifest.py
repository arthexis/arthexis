"""Manifest entries for Django app loading."""

DJANGO_APPS = [
    "apps.celery",
]

REQUIRES_APPS = [
    "apps.celery.beat_app.CeleryBeatConfig",
]
