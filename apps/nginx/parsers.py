"""Parsing helpers for nginx site configuration discovery."""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


SUBDOMAIN_PREFIX_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
NGINX_PROXY_PASS_RE = re.compile(r"proxy_pass\s+https?://[^:]+:(\d+)")
NGINX_SSL_LISTEN_RE = re.compile(r"listen\s+[^;]*\b443\b[^;]*ssl", re.IGNORECASE)
NGINX_SSL_CERTIFICATE_RE = re.compile(r"ssl_certificate\s+[^;]+;", re.IGNORECASE)
NGINX_IPV6_LISTEN_RE = re.compile(r"listen\s+\[::\][^;]*;", re.IGNORECASE)
NGINX_SERVER_NAME_RE = re.compile(r"server_name\s+([^;]+);")
NGINX_EXTERNAL_WEBSOCKETS_TOKEN = "proxy_set_header Connection $connection_upgrade;"


def parse_subdomain_prefixes(raw: str, *, strict: bool = True) -> list[str]:
    """Parse, normalize, and validate subdomain prefixes from comma/space-delimited text."""

    prefixes: list[str] = []
    seen: set[str] = set()
    invalid: list[str] = []
    for token in re.split(r"[,\s]+", raw or ""):
        candidate = token.strip().lower()
        if not candidate:
            continue
        if "." in candidate or not SUBDOMAIN_PREFIX_RE.match(candidate):
            invalid.append(candidate)
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        prefixes.append(candidate)
    if invalid and strict:
        raise ValidationError(
            _("Invalid subdomain prefixes: %(invalid)s"),
            params={"invalid": ", ".join(sorted(invalid))},
        )
    return prefixes


def _extract_proxy_port(content: str) -> int | None:
    """Extract the first valid upstream proxy port from nginx config content."""

    for match in NGINX_PROXY_PASS_RE.findall(content):
        try:
            port = int(match)
        except ValueError:
            continue
        if 1 <= port <= 65535:
            return port
    return None


def _extract_server_name(content: str) -> str:
    """Extract the first concrete server_name token from nginx config content."""

    for match in NGINX_SERVER_NAME_RE.findall(content):
        for token in match.split():
            token = token.strip()
            if not token or token == "_" or "*" in token or token.startswith("."):
                continue
            return token
    return ""


def _detect_https_enabled(content: str) -> bool:
    """Detect whether HTTPS directives are present in nginx config content."""

    if NGINX_SSL_LISTEN_RE.search(content):
        return True
    return bool(NGINX_SSL_CERTIFICATE_RE.search(content))


def _detect_ipv6_enabled(content: str) -> bool:
    """Detect whether IPv6 listen directives are present."""

    return bool(NGINX_IPV6_LISTEN_RE.search(content))


def _detect_external_websockets(content: str) -> bool:
    """Detect websocket header directives used for external websocket proxying."""

    return NGINX_EXTERNAL_WEBSOCKETS_TOKEN in content
