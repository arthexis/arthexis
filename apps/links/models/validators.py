"""Validation and slug utilities for links models."""

from __future__ import annotations

import secrets
from urllib.parse import urlparse

from django.utils.http import url_has_allowed_host_and_scheme


def generate_qr_slug() -> str:
    """Generate a short slug for :class:`QRRedirect`."""

    return secrets.token_urlsafe(6).rstrip("=")


def generate_short_slug() -> str:
    """Generate a short slug for :class:`ShortURL`."""

    return secrets.token_urlsafe(5).rstrip("=")


def _is_valid_redirect_target(value: str) -> bool:
    """Return ``True`` for an absolute http(s) URL or local absolute path."""

    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return url_has_allowed_host_and_scheme(value, allowed_hosts={parsed.netloc})
    if not parsed.scheme and not parsed.netloc and value.startswith("/"):
        return True
    return False
