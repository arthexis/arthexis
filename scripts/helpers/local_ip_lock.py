#!/usr/bin/env python3
"""Persist discovered local IP addresses to the ArtHExis lock directory."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


def _ensure_repo_on_path(base_dir: Path) -> None:
    """Ensure the repo root is on sys.path for module imports."""
    base_dir_str = str(base_dir)
    if base_dir_str not in sys.path:
        sys.path.insert(0, base_dir_str)


def _normalize_candidate_ip(candidate: str) -> str | None:
    """Normalize and validate a candidate IP address string."""

    cleaned = candidate.strip().strip("[]")
    if not cleaned:
        return None

    try:
        import ipaddress

        return str(ipaddress.ip_address(cleaned))
    except ValueError:
        return None


def _discover_local_ip_addresses_fallback() -> set[str]:
    """Discover local IP addresses without importing Django project settings."""

    addresses: set[str] = set()

    def _add(candidate: str) -> None:
        normalized = _normalize_candidate_ip(candidate)
        if normalized:
            addresses.add(normalized)

    for loopback in ("127.0.0.1", "::1"):
        _add(loopback)

    hostnames = [socket.gethostname(), socket.getfqdn()]
    for hostname in hostnames:
        if not hostname:
            continue
        try:
            _hostname, _aliases, hostname_ips = socket.gethostbyname_ex(hostname)
            for address in hostname_ips:
                _add(address)
        except Exception:
            pass

    try:
        command_output = subprocess.check_output(("hostname", "-I"), text=True)
        for token in command_output.split():
            _add(token)
    except Exception:
        pass

    return addresses


def _load_local_ip_lock_fallback(base_dir: Path) -> set[str]:
    """Read existing IP addresses from the local lock file."""

    lock_path = base_dir / ".locks" / "local_ips.lck"
    if not lock_path.exists():
        return set()

    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()

    addresses = payload.get("addresses", []) if isinstance(payload, dict) else payload
    if isinstance(addresses, str):
        addresses = addresses.splitlines()
    if not isinstance(addresses, list):
        return set()

    normalized: set[str] = set()
    for candidate in addresses:
        if candidate is None:
            continue
        normalized_ip = _normalize_candidate_ip(str(candidate))
        if normalized_ip:
            normalized.add(normalized_ip)
    return normalized


def _resolve_ip_helpers() -> tuple[Callable[[Path], set[str]], Callable[[], set[str]]]:
    """Return helper callables for loading and discovering local IP addresses."""

    try:
        from config.settings_helpers import discover_local_ip_addresses, load_local_ip_lock

        return load_local_ip_lock, discover_local_ip_addresses
    except ModuleNotFoundError as exc:
        if exc.name not in {"celery", "django", "django_celery_beat"}:
            raise
        return _load_local_ip_lock_fallback, _discover_local_ip_addresses_fallback


def main() -> int:
    """Generate or refresh the local IP lockfile payload."""

    if len(sys.argv) < 2:
        print("Usage: local_ip_lock.py <base-dir>", file=sys.stderr)
        return 1

    base_dir = Path(sys.argv[1]).resolve()
    if not base_dir.is_dir():
        print(f"Base dir not found: {base_dir}", file=sys.stderr)
        return 1
    _ensure_repo_on_path(base_dir)

    try:
        load_local_ip_lock, discover_local_ip_addresses = _resolve_ip_helpers()
        lock_dir = base_dir / ".locks"
        lock_path = lock_dir / "local_ips.lck"

        existing = load_local_ip_lock(base_dir)
        discovered = discover_local_ip_addresses()
        addresses = sorted(existing.union(discovered))

        payload = {
            "addresses": addresses,
            "updated_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return 0
    except Exception as exc:
        print(f"local_ip_lock warning: {exc}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
