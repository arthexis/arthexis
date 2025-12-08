"""Config package initialization."""

from .celery import app as celery

__all__ = ("celery",)
