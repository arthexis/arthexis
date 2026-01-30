from __future__ import annotations

import ipaddress
import socket
from typing import Optional

from django.conf import settings


def resolve_ws_scheme(
    *,
    ws_scheme: Optional[str] = None,
    use_tls: Optional[bool] = None,
    request=None,
) -> str:
    """Return the websocket scheme based on explicit settings or site config."""

    if ws_scheme:
        normalized = ws_scheme.strip().lower()
        if "://" in normalized:
            normalized = normalized.split("://", 1)[0]
        if normalized in {"ws", "wss"}:
            return normalized
        if normalized in {"http", "https"}:
            return "wss" if normalized == "https" else "ws"

    if use_tls is not None:
        return "wss" if use_tls else "ws"

    if request is not None:
        try:
            from config.request_utils import is_https_request

            if is_https_request(request):
                return "wss"
        except Exception:
            pass

    protocol = _site_http_protocol()
    return "wss" if protocol == "https" else "ws"


def _site_http_protocol() -> str:
    try:
        from apps.nginx.models import SiteConfiguration

        config = SiteConfiguration.objects.filter(enabled=True).order_by("pk").first()
        if config:
            if not getattr(config, "external_websockets", True):
                return "http"
            if config.protocol:
                return str(config.protocol).strip().lower()
    except Exception:
        pass

    return str(getattr(settings, "DEFAULT_HTTP_PROTOCOL", "http")).strip().lower()


def validate_ws_host(host: str | None) -> tuple[bool, str | None]:
    """Return whether a websocket host is safe to connect to."""

    if host is None:
        return False, "Simulator host is required."
    normalized = host.strip()
    if not normalized:
        return False, "Simulator host is required."
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    try:
        ip_value = ipaddress.ip_address(normalized)
    except ValueError:
        ip_value = None

    def _is_allowed(
        ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
    ) -> bool:
        return ip.is_loopback or ip.is_global

    if ip_value is not None:
        if _is_allowed(ip_value):
            return True, None
        return False, f"Simulator host '{host}' is not permitted."

    try:
        addrinfo = socket.getaddrinfo(normalized, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False, f"Simulator host '{host}' could not be resolved."
    except Exception:
        return False, f"Simulator host '{host}' could not be resolved."

    resolved_addresses: set[str] = set()
    for entry in addrinfo:
        sockaddr = entry[4]
        if isinstance(sockaddr, tuple) and sockaddr:
            resolved_addresses.add(sockaddr[0])
    if not resolved_addresses:
        return False, f"Simulator host '{host}' could not be resolved."

    for resolved in resolved_addresses:
        try:
            resolved_ip = ipaddress.ip_address(resolved)
        except ValueError:
            return False, f"Simulator host '{host}' resolved to an invalid address."
        if not _is_allowed(resolved_ip):
            return (
                False,
                f"Simulator host '{host}' resolved to a non-public address.",
            )
    return True, None
