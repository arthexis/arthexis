"""Helpers for configuring and consuming the mounted admin URL prefix."""

from __future__ import annotations

from django.conf import settings


def normalize_admin_url_path(raw_path: str) -> str:
    """Return a normalized admin path fragment with a trailing slash.

    The returned value is suitable for Django ``path(...)`` route prefixes, for
    example ``"admin/"`` or ``"control-panel/"``.
    """

    trimmed = raw_path.strip()
    if not trimmed:
        raise ValueError("Admin URL path cannot be blank.")

    normalized = trimmed.strip("/")
    if not normalized:
        raise ValueError("Admin URL path must include at least one segment.")

    return f"{normalized}/"


def admin_route(route_suffix: str = "") -> str:
    """Join the configured admin prefix with an optional route suffix."""

    suffix = route_suffix.lstrip("/")
    return f"{settings.ADMIN_URL_PATH}{suffix}"


def admin_mount_path() -> str:
    """Return the configured admin URL mount with a leading slash."""

    return f"/{settings.ADMIN_URL_PATH}"
