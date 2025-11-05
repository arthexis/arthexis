"""Utility helpers shared by :mod:`config.settings` and related tests."""

from __future__ import annotations

import contextlib
import ipaddress
import os
import socket
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable, Mapping, MutableMapping

from django.core.management.utils import get_random_secret_key
from django.http import request as http_request
from django.http.request import split_domain_port


__all__ = [
    "discover_local_ip_addresses",
    "extract_ip_from_host",
    "install_validate_host_with_subnets",
    "load_secret_key",
    "strip_ipv6_brackets",
    "validate_host_with_subnets",
]


def strip_ipv6_brackets(host: str) -> str:
    """Return ``host`` without IPv6 URL literal brackets."""

    if host.startswith("[") and host.endswith("]"):
        return host[1:-1]
    return host


def extract_ip_from_host(host: str):
    """Return an :mod:`ipaddress` object for ``host`` when possible."""

    candidate = strip_ipv6_brackets(host)
    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        domain, _port = split_domain_port(host)
        if domain and domain != host:
            candidate = strip_ipv6_brackets(domain)
            try:
                return ipaddress.ip_address(candidate)
            except ValueError:
                return None
    return None


def validate_host_with_subnets(host, allowed_hosts, original_validate=None):
    """Extend Django's host validation to honor subnet CIDR notation."""

    if original_validate is None:
        original_validate = http_request.validate_host

    ip = extract_ip_from_host(host)
    if ip is None:
        return original_validate(host, allowed_hosts)

    for pattern in allowed_hosts:
        try:
            network = ipaddress.ip_network(pattern)
        except ValueError:
            continue
        if ip in network:
            return True
    return original_validate(host, allowed_hosts)


def install_validate_host_with_subnets() -> None:
    """Monkeypatch Django's host validator to recognize subnet patterns."""

    original_validate = http_request.validate_host

    def _patched(host, allowed_hosts):
        return validate_host_with_subnets(host, allowed_hosts, original_validate)

    http_request.validate_host = _patched


def _normalize_candidate_ip(candidate: str) -> str | None:
    """Return a normalized IP string when *candidate* is valid."""

    if not candidate:
        return None

    normalized = strip_ipv6_brackets(candidate.strip())
    if not normalized:
        return None

    # Drop IPv6 zone identifiers (for example ``fe80::1%eth0``)
    if "%" in normalized:
        normalized = normalized.split("%", 1)[0]

    try:
        return ipaddress.ip_address(normalized).compressed
    except ValueError:
        return None


def _iter_command_addresses(command: Iterable[str]) -> Iterable[str]:
    """Yield IP addresses parsed from a command's stdout."""

    try:
        result = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            check=False,
            timeout=1.0,
        )
    except (FileNotFoundError, PermissionError, subprocess.SubprocessError):
        return

    if result.returncode != 0:
        return

    for token in result.stdout.split():
        normalized = _normalize_candidate_ip(token)
        if normalized:
            yield normalized


def _iter_ip_addr_show() -> Iterable[str]:
    """Yield interface addresses from the ``ip`` command when available."""

    commands = (
        ("ip", "-o", "-4", "addr", "show"),
        ("ip", "-o", "-6", "addr", "show"),
    )

    for command in commands:
        try:
            result = subprocess.run(
                list(command),
                capture_output=True,
                text=True,
                check=False,
                timeout=1.0,
            )
        except (FileNotFoundError, PermissionError, subprocess.SubprocessError):
            continue

        if result.returncode != 0:
            continue

        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            # ``<ifindex>: <ifname> <family> <address>/<prefix>``
            address = parts[3].split("/", 1)[0]
            normalized = _normalize_candidate_ip(address)
            if normalized:
                yield normalized


def _iter_metadata_addresses(env: Mapping[str, str]) -> Iterable[str]:
    """Yield IP addresses exposed by cloud metadata endpoints when available."""

    disable_env = env.get("DISABLE_METADATA_IP_DISCOVERY", "")
    if disable_env.strip().lower() in {"1", "true", "yes", "on"}:
        return

    if env.get("AWS_EC2_METADATA_DISABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
        return

    endpoints = (
        "http://169.254.169.254/latest/meta-data/local-ipv4",
        "http://169.254.169.254/latest/meta-data/public-ipv4",
        "http://169.254.169.254/latest/meta-data/local-ipv6",
        "http://169.254.169.254/latest/meta-data/ipv6",
    )

    for endpoint in endpoints:
        try:
            with urllib.request.urlopen(endpoint, timeout=0.5) as response:
                payload = response.read().decode("utf-8", "ignore").strip()
        except (urllib.error.URLError, OSError, ValueError):
            continue

        if not payload:
            continue

        for line in payload.splitlines():
            normalized = _normalize_candidate_ip(line)
            if normalized:
                yield normalized


def discover_local_ip_addresses(
    env: Mapping[str, str] | MutableMapping[str, str] | None = None,
) -> set[str]:
    """Return IP addresses associated with the current host.

    The discovery process aggregates several lightweight heuristics so the
    project continues to run even when specific mechanisms fail.  All
    collectors are best-effort and errors are swallowed to avoid blocking
    Django's startup.
    """

    if env is None:
        env = os.environ

    addresses: set[str] = set()

    def _add(candidate: str | None) -> None:
        normalized = _normalize_candidate_ip(candidate or "")
        if normalized:
            addresses.add(normalized)

    for loopback in ("127.0.0.1", "::1"):
        _add(loopback)

    hostnames: list[str] = []
    with contextlib.suppress(Exception):
        hostnames.append(socket.gethostname())
    with contextlib.suppress(Exception):
        hostnames.append(socket.getfqdn())

    for hostname in hostnames:
        if not hostname:
            continue

        with contextlib.suppress(Exception):
            _hostname, _aliases, addresses_list = socket.gethostbyname_ex(hostname)
            for address in addresses_list:
                _add(address)

        with contextlib.suppress(Exception):
            for info in socket.getaddrinfo(hostname, None):
                if len(info) < 5:
                    continue
                sock_address = info[4]
                if not sock_address:
                    continue
                _add(sock_address[0])

    for address in _iter_ip_addr_show():
        _add(address)

    for address in _iter_command_addresses(("hostname", "-I")):
        _add(address)

    for address in _iter_metadata_addresses(env):
        _add(address)

    return addresses


def load_secret_key(
    base_dir: Path,
    env: Mapping[str, str] | MutableMapping[str, str] | None = None,
    secret_file: Path | None = None,
) -> str:
    """Load the Django secret key from the environment or a persisted file."""

    if env is None:
        env = os.environ

    for env_var in ("DJANGO_SECRET_KEY", "SECRET_KEY"):
        value = env.get(env_var)
        if value:
            return value

    if secret_file is None:
        secret_file = base_dir / "locks" / "django-secret.key"

    with contextlib.suppress(OSError):
        stored_key = secret_file.read_text(encoding="utf-8").strip()
        if stored_key:
            return stored_key

    generated_key = get_random_secret_key()
    with contextlib.suppress(OSError):
        secret_file.parent.mkdir(parents=True, exist_ok=True)
        secret_file.write_text(generated_key, encoding="utf-8")

    return generated_key
