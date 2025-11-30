"""Helpers related to console Reference creation."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse


def _normalize_host(host: str | None) -> str:
    """Return a trimmed host string without surrounding brackets."""

    if not host:
        return ""
    host = host.strip()
    if host.startswith("[") and host.endswith("]"):
        return host[1:-1]
    return host


def host_is_local_loopback(host: str | None) -> bool:
    """Return ``True`` when the host string points to 127.0.0.1."""

    normalized = _normalize_host(host)
    if not normalized:
        return False
    try:
        return ipaddress.ip_address(normalized) == ipaddress.ip_address("127.0.0.1")
    except ValueError:
        return False


def url_targets_local_loopback(url: str | None) -> bool:
    """Return ``True`` when the parsed URL host equals 127.0.0.1."""

    if not url:
        return False
    parsed = urlparse(url)
    return host_is_local_loopback(parsed.hostname)


__all__ = ["host_is_local_loopback", "url_targets_local_loopback"]

