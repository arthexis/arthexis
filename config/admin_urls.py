"""Helpers for configuring and consuming the mounted admin URL prefix."""

from __future__ import annotations

import re

from django.conf import settings


_ADMIN_PATH_RE = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")
_RESERVED_ADMIN_PREFIXES = {"admindocs", "i18n", "__debug__"}


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

    if not _ADMIN_PATH_RE.fullmatch(normalized):
        raise ValueError("Admin URL path must contain only literal path segments.")

    first_segment = normalized.split("/", 1)[0]
    if first_segment in _RESERVED_ADMIN_PREFIXES:
        raise ValueError("Admin URL path conflicts with a reserved route prefix.")

    return f"{normalized}/"


def admin_route(route_suffix: str = "") -> str:
    """Join the configured admin prefix with an optional route suffix."""

    suffix = route_suffix.lstrip("/")
    prefix = normalize_admin_url_path(settings.ADMIN_URL_PATH)
    return f"{prefix}{suffix}"

