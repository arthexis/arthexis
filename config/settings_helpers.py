"""Utility helpers shared by :mod:`config.settings` and related tests."""

from __future__ import annotations

import contextlib
import ipaddress
import json
import os
import queue
import socket
import threading
from pathlib import Path
from typing import Callable, Mapping, MutableMapping
from urllib.parse import urlsplit

from django.core.management.utils import get_random_secret_key
from django.http import request as http_request
from django.http.request import split_domain_port
from apps.celery.utils import resolve_celery_shutdown_timeout


__all__ = [
    "extract_ip_from_host",
    "install_validate_host_with_subnets",
    "load_secret_key",
    "load_site_config_allowed_hosts",
    "normalize_site_host",
    "resolve_local_fqdn",
    "resolve_celery_shutdown_timeout",
    "should_probe_postgres",
    "strip_ipv6_brackets",
    "validate_host_with_subnets",
]


def normalize_site_host(candidate: str) -> str:
    """Return a normalized hostname extracted from *candidate*.

    ``candidate`` may be a bare hostname (``example.com``), host:port, or a full
    URL such as ``wss://example.com/path``.
    """

    value = (candidate or "").strip()
    if not value:
        return ""

    try:
        parsed = urlsplit(value if "://" in value else f"https://{value}")
    except ValueError:
        return ""
    hostname = (parsed.hostname or "").strip().strip(".").lower()
    return hostname


def load_site_config_allowed_hosts(base_dir: Path) -> list[str]:
    """Return hostnames declared in ``scripts/generated/nginx-sites.json``.

    The generated file is sourced from managed ``django.contrib.sites`` entries
    and provides a reboot-persistent source of hostnames that should be accepted
    by Django's host validation.
    """

    config_path = base_dir / "scripts" / "generated" / "nginx-sites.json"
    try:
        raw = config_path.read_text(encoding="utf-8")
        entries = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return []

    if not isinstance(entries, list):
        return []

    hosts: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        host = normalize_site_host(str(entry.get("domain") or ""))
        if not host or host in seen:
            continue
        seen.add(host)
        hosts.append(host)
    return hosts


def resolve_local_fqdn(
    hostname: str,
    resolver: Callable[[str], str] | None = None,
    timeout_seconds: float = 0.2,
) -> str:
    """Return the local FQDN while avoiding blocking reverse-DNS lookups.

    Some systems can block indefinitely when resolving ``socket.getfqdn``. This
    helper executes the resolver in a daemon thread and returns an empty string
    when the timeout is exceeded or resolution fails.
    """

    lookup = resolver or socket.getfqdn
    result_queue: queue.SimpleQueue[str | BaseException] = queue.SimpleQueue()

    def _run_lookup() -> None:
        try:
            result_queue.put(lookup(hostname))
        except (OSError, ValueError) as exc:
            result_queue.put(exc)

    worker = threading.Thread(target=_run_lookup, daemon=True)
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive() or result_queue.empty():
        return ""

    result = result_queue.get_nowait()
    if isinstance(result, BaseException):
        return ""
    return result.strip()


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



def should_probe_postgres(env: Mapping[str, str] | None = None) -> bool:
    """Return whether startup should attempt a PostgreSQL reachability probe.

    The probe is skipped unless PostgreSQL is explicitly configured, which
    prevents avoidable startup stalls on SQLite-based local setups.
    """

    source = os.environ if env is None else env

    configured_backend = str(source.get("ARTHEXIS_DB_BACKEND", "")).strip().lower()
    if configured_backend == "sqlite":
        return False
    if configured_backend == "postgres":
        return True

    postgres_env_vars = (
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
    )
    if any(str(source.get(name, "")).strip() for name in postgres_env_vars):
        return True

    database_url = str(source.get("DATABASE_URL", "")).strip().lower()
    if database_url.startswith(("postgres://", "postgresql://")):
        return True

    return False


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
        secret_file = base_dir / ".locks" / "django-secret.key"

    with contextlib.suppress(OSError):
        stored_key = secret_file.read_text(encoding="utf-8").strip()
        if stored_key:
            return stored_key

    generated_key = get_random_secret_key()
    with contextlib.suppress(OSError):
        secret_file.parent.mkdir(parents=True, exist_ok=True)
        secret_file.write_text(generated_key, encoding="utf-8")

    return generated_key
