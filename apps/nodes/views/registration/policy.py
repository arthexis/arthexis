"""Centralized security policy for node registration workflows.

Defaults:
- Visitor URL allow-list is disabled when no suffixes are configured.
- Only HTTPS visitor URLs are accepted.
- Invalid signatures may fall back to authenticated user authorization.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from django.conf import settings


def get_allowed_visitor_suffixes() -> tuple[str, ...]:
    """Return configured visitor hostname suffix allow-list."""

    suffixes = getattr(settings, "VISITOR_ALLOWED_HOST_SUFFIXES", ())
    if isinstance(suffixes, str):
        suffixes = (suffixes,)
    return tuple(value for value in suffixes if value)


def is_allowed_visitor_url(url: str) -> bool:
    """Return True when ``url`` passes HTTPS and hostname suffix policy."""

    try:
        parsed = urlsplit(url)
    except Exception:
        return False
    if parsed.scheme != "https" or not parsed.hostname:
        return False

    suffixes = get_allowed_visitor_suffixes()
    if not suffixes:
        return True
    hostname = parsed.hostname.lower()
    return any(
        hostname == suffix.lower() or hostname.endswith(f".{suffix.lower()}")
        for suffix in suffixes
    )


def allow_authenticated_signature_fallback() -> bool:
    """Return whether authenticated users may proceed after signature failures."""

    return bool(getattr(settings, "VISITOR_ALLOW_AUTH_SIGNATURE_FALLBACK", True))
