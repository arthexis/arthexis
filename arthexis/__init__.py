"""Arthexis 2.0 Django project."""

from arthexis.celery import app as celery_app

__all__ = ["celery_app"]
