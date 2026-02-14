#!/usr/bin/env python3
"""Persist local IP addresses to a lock file for ALLOWED_HOSTS."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import urllib.error
import urllib.request
from pathlib import Path


def _normalize_candidate_ip(candidate: str) -> str | None:
    if not candidate:
        return None

    normalized = candidate.strip()
    if not normalized:
        return None

    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]

    if "%" in normalized:
        normalized = normalized.split("%", 1)[0]

    try:
        import ipaddress

        return ipaddress.ip_address(normalized).compressed
    except ValueError:
        return None


def _iter_command_addresses(command: list[str]) -> list[str]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=1.0,
        )
    except (FileNotFoundError, PermissionError, subprocess.SubprocessError):
        return []

    if result.returncode != 0:
        return []

    return [candidate for token in result.stdout.split() if (candidate := _normalize_candidate_ip(token))]


def _iter_ip_addr_show() -> list[str]:
    addresses: list[str] = []
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
            address = parts[3].split("/", 1)[0]
            normalized = _normalize_candidate_ip(address)
            if normalized:
                addresses.append(normalized)

    return addresses


def _iter_metadata_addresses(env: dict[str, str]) -> list[str]:
    disable_env = env.get("DISABLE_METADATA_IP_DISCOVERY", "")
    if disable_env.strip().lower() in {"1", "true", "yes", "on"}:
        return []

    if env.get("AWS_EC2_METADATA_DISABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
        return []

    endpoints = (
        "http://169.254.169.254/latest/meta-data/local-ipv4",
        "http://169.254.169.254/latest/meta-data/public-ipv4",
        "http://169.254.169.254/latest/meta-data/local-ipv6",
        "http://169.254.169.254/latest/meta-data/ipv6",
    )

    addresses: list[str] = []
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
                addresses.append(normalized)

    return addresses


def discover_local_ip_addresses(env: dict[str, str]) -> set[str]:
    addresses: set[str] = set()

    def _add(candidate: str | None) -> None:
        normalized = _normalize_candidate_ip(candidate or "")
        if normalized:
            addresses.add(normalized)

    for loopback in ("127.0.0.1", "::1"):
        _add(loopback)

    hostnames: list[str] = []
    try:
        hostnames.append(socket.gethostname())
    except OSError:
        pass
    try:
        hostnames.append(socket.getfqdn())
    except OSError:
        pass

    for hostname in hostnames:
        if not hostname:
            continue

        try:
            _hostname, _aliases, addresses_list = socket.gethostbyname_ex(hostname)
            for address in addresses_list:
                _add(address)
        except OSError:
            pass

        try:
            for info in socket.getaddrinfo(hostname, None):
                if len(info) < 5:
                    continue
                sock_address = info[4]
                if not sock_address:
                    continue
                _add(sock_address[0])
        except OSError:
            pass

    for address in _iter_ip_addr_show():
        _add(address)

    for address in _iter_command_addresses(["hostname", "-I"]):
        _add(address)

    for address in _iter_metadata_addresses(env):
        _add(address)

    return addresses


def write_lock_file(base_dir: Path, addresses: set[str]) -> Path:
    lock_dir = base_dir / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = lock_dir / "local_ips.lck"
    payload = sorted(addresses)
    lock_file.write_text(json.dumps(payload), encoding="utf-8")
    return lock_file


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_dir", nargs="?", default=os.getcwd())
    args = parser.parse_args()

    base_dir = Path(args.base_dir).resolve()
    addresses = discover_local_ip_addresses(dict(os.environ))
    write_lock_file(base_dir, addresses)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
