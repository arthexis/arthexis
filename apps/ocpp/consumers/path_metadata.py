"""Helpers for persisting bounded websocket path metadata."""

from __future__ import annotations

from django.db import models


def bounded_last_path(scope: dict | None, model: type[models.Model]) -> str:
    """Return the ASGI path trimmed to the target model's ``last_path`` field."""
    path = (scope or {}).get("path") or ""
    if isinstance(path, bytes):
        path = path.decode("utf-8", errors="replace")
    elif not isinstance(path, str):
        path = str(path)
    max_length = model._meta.get_field("last_path").max_length
    if max_length is not None:
        return path[:max_length]
    return path
