"""Security and host/origin validation settings."""

import ipaddress
import socket
from urllib.parse import urlsplit

from django.core.exceptions import DisallowedHost
from django.http import request as http_request
from django.http.request import split_domain_port
from django.middleware.csrf import CsrfViewMiddleware

from config.request_utils import is_https_request
from config.settings_helpers import (
    extract_ip_from_host,
    install_validate_host_with_subnets,
    load_site_config_allowed_hosts,
    resolve_local_fqdn,
    strip_ipv6_brackets,
)

from .base import BASE_DIR

install_validate_host_with_subnets()

ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
    "testserver",
    "10.42.0.1",
    "10.42.0.0/16",
    "192.168.0.0/16",
    "arthexis.com",
    "www.arthexis.com",
    "m.arthexis.com",
    ".arthexis.com",
]

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

_DEFAULT_PORTS = {"http": "80", "https": "443"}


def _get_allowed_hosts() -> list[str]:
    """Return ALLOWED_HOSTS from Django settings when available."""

    from django.conf import settings as django_settings

    configured = getattr(django_settings, "ALLOWED_HOSTS", None)
    if configured is None:
        return ALLOWED_HOSTS
    return list(configured)


def _iter_local_hostnames(hostname: str, fqdn: str | None = None) -> list[str]:
    """Return unique hostname variants for the current machine."""

    hostnames: list[str] = []
    seen: set[str] = set()

    def _append(candidate: str | None) -> None:
        if not candidate:
            return
        normalized = candidate.strip()
        if not normalized or normalized in seen:
            return
        hostnames.append(normalized)
        seen.add(normalized)

    _append(hostname)
    _append(fqdn)
    if hostname and "." not in hostname:
        _append(f"{hostname}.local")

    return hostnames


def _host_is_allowed(host: str, allowed_hosts: list[str]) -> bool:
    """Check if a host is allowed, handling values that include a port."""

    if http_request.validate_host(host, allowed_hosts):
        return True
    domain, _port = split_domain_port(host)
    if domain and domain != host:
        return http_request.validate_host(domain, allowed_hosts)
    return False


def _parse_forwarded_header(header_value: str) -> list[dict[str, str]]:
    """Parse RFC-7239 Forwarded entries into key/value dictionaries."""

    entries: list[dict[str, str]] = []
    if not header_value:
        return entries
    for forwarded_part in header_value.split(","):
        entry: dict[str, str] = {}
        for element in forwarded_part.split(";"):
            if "=" not in element:
                continue
            key, value = element.split("=", 1)
            entry[key.strip().lower()] = value.strip().strip('"')
        if entry:
            entries.append(entry)
    return entries


def _get_request_scheme(request, forwarded_entry: dict[str, str] | None = None) -> str:
    """Return the scheme used by the client, honoring proxy headers."""

    if forwarded_entry and forwarded_entry.get("proto", "").lower() in {
        "http",
        "https",
    }:
        return forwarded_entry["proto"].lower()

    if is_https_request(request):
        return "https"

    forwarded_proto = request.META.get("HTTP_X_FORWARDED_PROTO", "")
    if forwarded_proto:
        candidate = forwarded_proto.split(",")[0].strip().lower()
        if candidate in {"http", "https"}:
            return candidate

    forwarded_header = request.META.get("HTTP_FORWARDED", "")
    for parsed_entry in _parse_forwarded_header(forwarded_header):
        candidate = parsed_entry.get("proto", "").lower()
        if candidate in {"http", "https"}:
            return candidate

    return "http"


def _normalize_origin_tuple(
    scheme: str | None, host: str
) -> tuple[str, str, str | None] | None:
    """Normalize scheme/host input to a tuple suitable for comparisons."""

    if not scheme or scheme.lower() not in {"http", "https"}:
        return None
    domain, port = split_domain_port(host)
    normalized_host = strip_ipv6_brackets(domain.strip().lower())
    if not normalized_host:
        return None
    normalized_port = port.strip() if isinstance(port, str) else port
    if not normalized_port:
        normalized_port = _DEFAULT_PORTS.get(scheme.lower())
    if normalized_port is not None:
        normalized_port = str(normalized_port)
    return scheme.lower(), normalized_host, normalized_port


def _normalized_request_origin(origin: str) -> tuple[str, str, str | None] | None:
    """Normalize a request Origin header value."""

    parsed = urlsplit(origin)
    if not parsed.scheme or not parsed.hostname:
        return None
    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower()
    port = str(parsed.port) if parsed.port is not None else _DEFAULT_PORTS.get(scheme)
    return scheme, host, port


def _iter_candidate_hosts(request, default_scheme: str) -> list[tuple[str | None, str]]:
    """Yield origin candidates extracted from forwarding and request headers."""

    candidates: list[tuple[str | None, str]] = []

    forwarded_header = request.META.get("HTTP_FORWARDED", "")
    for forwarded_entry in _parse_forwarded_header(forwarded_header):
        host = forwarded_entry.get("host", "").strip()
        scheme = _get_request_scheme(request, forwarded_entry)
        candidates.append((scheme, host))

    forwarded_host = request.META.get("HTTP_X_FORWARDED_HOST", "")
    if forwarded_host:
        host = forwarded_host.split(",")[0].strip()
        candidates.append((default_scheme, host))

    try:
        good_host = request.get_host()
    except DisallowedHost:
        good_host = ""
    if good_host:
        candidates.append((default_scheme, good_host))

    return candidates


def _normalize_candidate(
    scheme: str | None,
    host: str,
    allowed_hosts: list[str],
    seen: set[tuple[str, str, str | None]],
) -> tuple[str, str, str | None] | None:
    """Return a normalized host candidate when it is valid and unique."""

    if not scheme or not host:
        return None
    normalized = _normalize_origin_tuple(scheme, host)
    if normalized is None:
        return None
    if not _host_is_allowed(host, allowed_hosts):
        return None
    if normalized in seen:
        return None
    seen.add(normalized)
    return normalized


def _candidate_origin_tuples(
    request, allowed_hosts: list[str]
) -> list[tuple[str, str, str | None]]:
    """Build normalized origin tuples for a request."""

    default_scheme = _get_request_scheme(request)
    seen: set[tuple[str, str, str | None]] = set()
    candidates: list[tuple[str, str, str | None]] = []

    for scheme, host in _iter_candidate_hosts(request, default_scheme):
        normalized = _normalize_candidate(scheme, host, allowed_hosts, seen)
        if normalized is not None:
            candidates.append(normalized)

    return candidates


def _hosts_share_allowed_subnet(
    first_host: str, second_host: str, allowed_hosts: list[str]
) -> bool:
    """Return True when both hosts resolve to IPs within the same allowed subnet."""

    first_ip = extract_ip_from_host(first_host)
    second_ip = extract_ip_from_host(second_host)
    if not first_ip or not second_ip:
        return False
    for pattern in allowed_hosts:
        try:
            network = ipaddress.ip_network(pattern)
        except ValueError:
            continue
        if first_ip in network and second_ip in network:
            return True
    return False


def _origin_in_candidates(
    origin: tuple[str, str, str | None],
    request,
    allowed_hosts: list[str],
) -> bool:
    """Return True if the given origin matches a normalized request candidate."""

    for candidate in _candidate_origin_tuples(request, allowed_hosts):
        if candidate == origin:
            return True
        if (
            candidate[0] == origin[0]
            and candidate[2] == origin[2]
            and _hosts_share_allowed_subnet(candidate[1], origin[1], allowed_hosts)
        ):
            return True
    return False


def _origin_verified_with_subnets(self, request):
    """Extend Django CSRF origin checks to allow configured subnets."""

    request_origin = request.META["HTTP_ORIGIN"]
    allowed_hosts = _get_allowed_hosts()
    normalized_origin = _normalized_request_origin(request_origin)
    if normalized_origin is None:
        return _original_origin_verified(self, request)

    if _origin_in_candidates(normalized_origin, request, allowed_hosts):
        return True
    return _original_origin_verified(self, request)


def _check_referer_with_forwarded(self, request):
    """Run referer checks against forwarded host/scheme candidates."""

    referer = request.META.get("HTTP_REFERER")
    if referer is None:
        return _original_check_referer(self, request)

    try:
        parsed = urlsplit(referer)
    except ValueError:
        return _original_check_referer(self, request)

    if "" in (parsed.scheme, parsed.netloc):
        return _original_check_referer(self, request)

    if parsed.scheme.lower() != "https":
        return _original_check_referer(self, request)

    normalized_referer = _normalize_origin_tuple(parsed.scheme.lower(), parsed.netloc)
    if normalized_referer is None:
        return _original_check_referer(self, request)

    allowed_hosts = _get_allowed_hosts()

    if _origin_in_candidates(normalized_referer, request, allowed_hosts):
        return

    return _original_check_referer(self, request)


_local_hostname = socket.gethostname().strip()
_local_fqdn = resolve_local_fqdn(_local_hostname)

for host in _iter_local_hostnames(_local_hostname, _local_fqdn):
    if host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(host)

for host in load_site_config_allowed_hosts(BASE_DIR):
    if host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(host)

# Allow CSRF origin verification for hosts within allowed subnets.
_original_origin_verified = CsrfViewMiddleware._origin_verified
_original_check_referer = CsrfViewMiddleware._check_referer
CsrfViewMiddleware._origin_verified = _origin_verified_with_subnets
CsrfViewMiddleware._check_referer = _check_referer_with_forwarded

CSRF_FAILURE_VIEW = "apps.sites.views.csrf_failure"

# Allow staff TODO pages to embed internal admin views inside iframes.
X_FRAME_OPTIONS = "SAMEORIGIN"
