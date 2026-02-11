"""Network and address derivation helpers for registration views."""

from __future__ import annotations

import ipaddress
import socket
import ssl
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
import urllib3
from django.conf import settings
from django.http.request import split_domain_port

from config.request_utils import is_https_request


def get_client_ip(request) -> str:
    """Return the originating client IP extracted from request metadata."""

    remote_addr = request.META.get("REMOTE_ADDR", "")
    trusted_proxies = getattr(settings, "TRUSTED_PROXIES", ())
    if isinstance(trusted_proxies, str):
        trusted_proxies = (trusted_proxies,)
    trusted_proxy_set = {value.strip() for value in trusted_proxies if value and value.strip()}

    if remote_addr in trusted_proxy_set:
        forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded_for:
            for value in forwarded_for.split(","):
                candidate = value.strip()
                if candidate:
                    return candidate
    return remote_addr


def _get_route_address(remote_ip: str, port: int) -> str:
    """Return the local source address used to route traffic to ``remote_ip``."""

    if not remote_ip:
        return ""
    try:
        parsed = ipaddress.ip_address(remote_ip)
    except ValueError:
        return ""

    try:
        target_port = int(port)
    except (TypeError, ValueError):
        target_port = 1
    if target_port <= 0 or target_port > 65535:
        target_port = 1

    family = socket.AF_INET6 if parsed.version == 6 else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as sock:
            if family == socket.AF_INET6:
                sock.connect((remote_ip, target_port, 0, 0))
            else:
                sock.connect((remote_ip, target_port))
            return sock.getsockname()[0]
    except OSError:
        return ""


def _get_host_ip(request) -> str:
    """Return host header value when it contains an IP literal."""

    try:
        host = request.get_host()
    except Exception:
        return ""
    if not host:
        return ""
    domain, _ = split_domain_port(host)
    if not domain:
        return ""
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        return ""
    return domain


def _get_host_domain(request) -> str:
    """Return host header value when it contains a DNS domain."""

    try:
        host = request.get_host()
    except Exception:
        return ""
    if not host:
        return ""
    domain, _ = split_domain_port(host)
    if not domain or domain.lower() == "localhost":
        return ""
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        return domain
    return ""


def _normalize_port(value: str | int | None) -> int | None:
    """Coerce ``value`` to a valid TCP port integer when possible."""

    if value in (None, ""):
        return None
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    if port <= 0 or port > 65535:
        return None
    return port


def _get_host_port(request) -> int | None:
    """Return best-effort inferred host port for request context."""

    forwarded_port = request.headers.get("X-Forwarded-Port") or request.META.get(
        "HTTP_X_FORWARDED_PORT"
    )
    port = _normalize_port(forwarded_port)
    if port:
        return port

    try:
        host = request.get_host()
    except Exception:
        host = request.META.get("HTTP_HOST", "")
    if host:
        _, host_port = split_domain_port(host)
        port = _normalize_port(host_port)
        if port:
            return port

    forwarded_proto = request.headers.get("X-Forwarded-Proto") or request.META.get(
        "HTTP_X_FORWARDED_PROTO", ""
    )
    if forwarded_proto:
        scheme = forwarded_proto.split(",")[0].strip().lower()
        if scheme == "https":
            return 443
        if scheme == "http":
            return 80

    if is_https_request(request):
        return 443

    scheme = getattr(request, "scheme", "")
    if scheme.lower() == "https":
        return 443
    if scheme.lower() == "http":
        return 80
    return None


def get_advertised_address(request, node) -> str:
    """Return the address a peer should use to reach ``node``."""

    client_ip = get_client_ip(request)
    route_address = _get_route_address(client_ip, node.port)
    if route_address:
        return route_address
    host_ip = _get_host_ip(request)
    if host_ip:
        return host_ip
    return node.get_primary_contact() or node.address or node.hostname


def append_token(url: str, token: str) -> str:
    """Append ``token`` query arg to URL while preserving prior query params."""

    if not (url and token):
        return url
    try:
        parsed = urlsplit(url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["token"] = token
        return urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urlencode(query, doseq=True),
                parsed.fragment,
            )
        )
    except Exception:
        return url


def iter_port_fallback_urls(base_url: str):
    """Yield ``base_url`` and supported fallback URLs for alternate ports."""

    yield base_url
    try:
        parsed = urlsplit(base_url)
    except Exception:
        return
    if not parsed.hostname or parsed.port != 8888:
        return

    netloc = parsed.hostname
    if ":" in netloc and not netloc.startswith("["):
        netloc = f"[{netloc}]"
    yield urlunsplit(
        (
            parsed.scheme or "https",
            f"{netloc}:8000",
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    )


def _build_host_header(parsed_url) -> str:
    hostname = parsed_url.hostname or ""
    if not hostname:
        return ""
    if parsed_url.port and parsed_url.port != 443:
        return f"{hostname}:{parsed_url.port}"
    return hostname


def _build_ip_url(parsed_url, ip_str: str) -> str:
    netloc = f"[{ip_str}]" if ":" in ip_str else ip_str
    if parsed_url.port:
        netloc = f"{netloc}:{parsed_url.port}"
    return urlunsplit(
        (
            parsed_url.scheme or "https",
            netloc,
            parsed_url.path,
            parsed_url.query,
            parsed_url.fragment,
        )
    )


@dataclass(frozen=True)
class PublicTarget:
    """Resolved public URL target with SNI and Host-header metadata."""

    url: str
    host_header: str
    server_hostname: str


class HostNameSSLAdapter(requests.adapters.HTTPAdapter):
    """HTTP adapter that preserves hostname verification for IP URLs."""

    def __init__(self, server_hostname: str, **kwargs: Any) -> None:
        self._server_hostname = server_hostname
        super().__init__(**kwargs)

    def init_poolmanager(  # type: ignore[override]
        self, connections: int, maxsize: int, block: bool = False, **pool_kwargs: Any
    ) -> None:
        pool_kwargs.setdefault("server_hostname", self._server_hostname)
        pool_kwargs.setdefault("assert_hostname", self._server_hostname)
        pool_kwargs.setdefault("cert_reqs", ssl.CERT_REQUIRED)
        pool_kwargs.setdefault("ca_certs", requests.certs.where())
        self.poolmanager = urllib3.PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            **pool_kwargs,
        )


def get_public_targets(url: str) -> list[PublicTarget]:
    """Resolve ``url`` hostname and return safe public IP targets."""

    try:
        parsed = urlsplit(url)
    except Exception:
        return []
    if parsed.scheme != "https" or not parsed.hostname:
        return []

    try:
        addrinfo_list = socket.getaddrinfo(parsed.hostname, parsed.port or 443)
    except OSError:
        return []

    resolved_ips: list[str] = []
    for family, _, _, _, sockaddr in addrinfo_list:
        if family not in (socket.AF_INET, socket.AF_INET6):
            return []
        ip_str = sockaddr[0]
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError:
            return []
        if not ip_obj.is_global or ip_obj.is_multicast:
            return []
        if ip_str not in resolved_ips:
            resolved_ips.append(ip_str)

    host_header = _build_host_header(parsed)
    return [
        PublicTarget(
            url=_build_ip_url(parsed, ip_str),
            host_header=host_header,
            server_hostname=parsed.hostname,
        )
        for ip_str in resolved_ips
    ]
