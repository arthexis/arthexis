"""Utility helpers shared by :mod:`config.settings` and related tests."""

from __future__ import annotations

import contextlib
import ipaddress
import os
from pathlib import Path
from typing import Mapping, MutableMapping

from django.core.management.utils import get_random_secret_key
from django.http import request as http_request
from django.http.request import split_domain_port


__all__ = [
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
