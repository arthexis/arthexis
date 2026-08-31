"""Release publish HTTP adapter public API."""

from django.apps import apps as django_apps

from .exceptions import DirtyRepository, PublishPending

if django_apps.is_installed("apps.repos"):
    from .views import PUBLISH_STEPS, release_progress
else:
    PUBLISH_STEPS = ()

__all__ = [
    "DirtyRepository",
    "PublishPending",
    "PUBLISH_STEPS",
]

if django_apps.is_installed("apps.repos"):
    __all__.append("release_progress")
