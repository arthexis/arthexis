from __future__ import annotations

from django.apps import apps as django_apps
from django.http import Http404

from .logs import _append_log, _release_log_name, _resolve_release_log_dir
from .release_publish.exceptions import DirtyRepository, PublishPending

if django_apps.is_installed("apps.repos"):
    from .release_publish.views import PUBLISH_STEPS, release_progress
else:
    PUBLISH_STEPS = ()

    def release_progress(request, pk: int, action: str):
        raise Http404("Release publishing requires the Repos app.")

__all__ = [
    "_append_log",
    "_release_log_name",
    "_resolve_release_log_dir",
    "DirtyRepository",
    "PUBLISH_STEPS",
    "PublishPending",
    "release_progress",
]
