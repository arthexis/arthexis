"""Helpers for redacting sensitive values in registration logs."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from cryptography.hazmat.primitives import hashes
from django.conf import settings


def redact_mac(mac: str | None) -> str:
    """Return a deterministic redaction token for a MAC address."""

    if not mac:
        return ""
    mac_str = "".join(char.lower() for char in str(mac) if char.isalnum())
    if not mac_str:
        return "***REDACTED***"
    digest = hashes.Hash(hashes.SHA256())
    digest.update(mac_str.encode("utf-8"))
    mac_hash = digest.finalize().hex()
    return f"***REDACTED***-{mac_hash[:12]}"


def redact_value(value: str | None) -> str:
    """Return a deterministic redaction token for any sensitive value."""

    if not value:
        return ""
    digest = hashes.Hash(hashes.SHA256())
    digest.update(settings.SECRET_KEY.encode("utf-8"))
    digest.update(value.encode("utf-8"))
    value_hash = digest.finalize().hex()
    return f"***REDACTED***-{value_hash[:12]}"


def redact_token_value(token: str | None) -> str:
    """Return a redacted representation of an authentication token."""

    return redact_value(token)


def redact_network_value(value: str | None) -> str:
    """Return a redacted representation of a hostname or address."""

    return redact_value(value)


def redact_url_token(url: str) -> str:
    """Return ``url`` with any ``token`` query parameter redacted."""

    if not url:
        return ""
    try:
        parsed = urlsplit(url)
        query_items = list(parse_qsl(parsed.query, keep_blank_values=True))
        if not query_items:
            return url
        updated = []
        changed = False
        for key, value in query_items:
            if key == "token":
                updated.append((key, "***REDACTED***"))
                changed = True
            else:
                updated.append((key, value))
        if not changed:
            return url
        return urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urlencode(updated, doseq=True),
                parsed.fragment,
            )
        )
    except Exception:
        return "***REDACTED-URL-ON-PARSE-ERROR***"
