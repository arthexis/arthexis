"""Helpers for recording WiFi usage and managing access controls."""

from __future__ import annotations

import datetime
import ipaddress
import logging
import re
import subprocess
from pathlib import Path
from typing import Iterable, TYPE_CHECKING

from django.apps import apps
from django.utils import timezone

if TYPE_CHECKING:  # pragma: no cover - import for type checking only
    from .models import WiFiLead as WiFiLeadModel

logger = logging.getLogger(__name__)

AP_NETWORK = ipaddress.ip_network("10.42.0.0/16")
LEASE_PATHS: list[Path] = [
    Path("/var/lib/NetworkManager/dnsmasq-shared.leases"),
    Path("/var/lib/NetworkManager/dnsmasq-wlan0.leases"),
    Path("/var/lib/misc/dnsmasq.leases"),
]
AP_INTERFACE = "wlan0"
INPUT_ALLOW_CHAIN = "GELECTRIIC_AP_INPUT_ALLOW"
FORWARD_ALLOW_CHAIN = "GELECTRIIC_AP_FORWARD_ALLOW"

_MAC_PATTERN = re.compile(r"^[0-9A-Fa-f]{2}([:-][0-9A-Fa-f]{2}){5}$")


def _client_ip(request) -> str | None:
    """Return the client IP when it belongs to the access-point network."""

    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    candidate = forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR", "")
    try:
        addr = ipaddress.ip_address(candidate)
    except ValueError:
        return None
    return candidate if addr in AP_NETWORK else None


def _read_leases() -> Iterable[tuple[str, str, str]]:
    """Yield ``(expiry, mac, ip)`` tuples from available dnsmasq leases files."""

    for path in LEASE_PATHS:
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line:
                        continue
                    parts = line.split()
                    if len(parts) < 3:
                        continue
                    yield parts[0], parts[1], parts[2]
        except FileNotFoundError:
            continue
        except OSError as exc:  # pragma: no cover - informational
            logger.warning("Unable to read lease file %s: %s", path, exc)


def _lease_details(ip: str) -> tuple[str | None, datetime.datetime | None]:
    """Return the MAC address and expiry for the provided IP, if known."""

    for expiry, mac, lease_ip in _read_leases():
        if lease_ip != ip:
            continue
        mac_norm = _normalize_mac(mac)
        lease_until = None
        try:
            raw_expiry = int(expiry)
        except ValueError:
            pass
        else:
            if raw_expiry > 0:
                lease_until = timezone.localtime(
                    datetime.datetime.fromtimestamp(raw_expiry, tz=datetime.timezone.utc)
                )
        return mac_norm, lease_until
    return None, None


def _normalize_mac(mac: str | None) -> str | None:
    if not mac:
        return None
    cleaned = mac.strip().replace("-", ":").replace(".", "")
    if ":" not in cleaned and len(cleaned) == 12:
        cleaned = ":".join(cleaned[i : i + 2] for i in range(0, 12, 2))
    cleaned = cleaned.lower()
    if not _MAC_PATTERN.match(cleaned):
        return None
    parts = cleaned.split(":")
    try:
        canonical = ":".join(f"{int(part, 16):02x}" for part in parts)
    except ValueError:
        return None
    return canonical


def _run_command(args: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(args, capture_output=True, text=True, check=False)
    except (FileNotFoundError, PermissionError) as exc:  # pragma: no cover - environment specific
        logger.warning("Unable to execute %s: %s", args[0], exc)
        return None


def _mac_from_neigh(ip: str) -> str | None:
    for command in (
        ["ip", "neigh", "show", ip, "dev", AP_INTERFACE],
        ["ip", "neigh", "show", ip],
        ["arp", "-n", ip],
    ):
        result = _run_command(command)
        if not result or result.returncode != 0:
            continue
        for line in result.stdout.splitlines():
            if "lladdr" in line:
                tokens = line.split()
                try:
                    index = tokens.index("lladdr")
                except ValueError:
                    continue
                if index + 1 < len(tokens):
                    mac = _normalize_mac(tokens[index + 1])
                    if mac:
                        return mac
            tokens = line.split()
            if len(tokens) >= 3 and tokens[0] == ip:
                mac = _normalize_mac(tokens[2])
                if mac:
                    return mac
    return None


def _run_iptables(args: list[str]) -> subprocess.CompletedProcess[str] | None:
    return _run_command(["iptables", *args])


def allow_client_internet(mac: str) -> None:
    mac = mac.lower()
    result = _run_iptables(["-C", FORWARD_ALLOW_CHAIN, "-m", "mac", "--mac-source", mac, "-j", "ACCEPT"])
    if result is None:
        return
    if result.returncode == 0:
        return
    _run_iptables(["-I", FORWARD_ALLOW_CHAIN, "1", "-m", "mac", "--mac-source", mac, "-j", "ACCEPT"])


def allow_staff_ports(mac: str) -> None:
    mac = mac.lower()
    result = _run_iptables(["-C", INPUT_ALLOW_CHAIN, "-m", "mac", "--mac-source", mac, "-j", "ACCEPT"])
    if result is None:
        return
    if result.returncode == 0:
        return
    _run_iptables(["-I", INPUT_ALLOW_CHAIN, "1", "-m", "mac", "--mac-source", mac, "-j", "ACCEPT"])


def handle_user_login(request, user) -> None:
    ip = _client_ip(request)
    if not ip:
        return
    mac, lease_until = _lease_details(ip)
    if not mac:
        mac = _mac_from_neigh(ip)
    mac_storage = mac.upper() if mac else ""
    WiFiLead = apps.get_model("core", "WiFiLead")
    defaults = {
        "path": request.get_full_path(),
        "referer": request.META.get("HTTP_REFERER", ""),
        "user_agent": request.META.get("HTTP_USER_AGENT", ""),
        "ip_address": ip,
        "mac_address": mac_storage,
        "last_seen": timezone.now(),
        "lease_expires": lease_until,
    }
    WiFiLead.objects.update_or_create(user=user, defaults=defaults)
    if mac:
        allow_client_internet(mac)
        if getattr(user, "is_staff", False):
            allow_staff_ports(mac)
