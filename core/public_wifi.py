"""Utilities for managing public Wi-Fi access control."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterable

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

_MAC_RE = re.compile(r"(?P<mac>([0-9a-f]{2}:){5}[0-9a-f]{2})", re.IGNORECASE)


def _lock_dir() -> Path:
    return Path(settings.BASE_DIR) / "locks"


def _mode_lock() -> Path:
    return _lock_dir() / "public_wifi_mode.lck"


def _allowlist_path() -> Path:
    return _lock_dir() / "public_wifi_allow.list"


def _normalize_mac(mac: str) -> str:
    return mac.strip().lower()


def resolve_mac_address(ip_address: str | None) -> str | None:
    """Attempt to resolve the MAC address for ``ip_address``.

    The lookup prefers ``ip neigh`` and falls back to ``arp`` when available.
    Returns ``None`` when the MAC cannot be determined.
    """

    if not ip_address:
        return None

    commands: Iterable[list[str]] = []
    ip_cmd = shutil.which("ip")
    if ip_cmd:
        commands = [[ip_cmd, "neigh", "show", ip_address]]
    arp_cmd = shutil.which("arp")
    if arp_cmd:
        commands = list(commands) + [[arp_cmd, "-n", ip_address]]
    for command in commands:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=1,
            )
        except Exception:  # pragma: no cover - defensive
            continue
        if result.returncode != 0:
            continue
        match = _MAC_RE.search(result.stdout)
        if match:
            mac = match.group("mac")
            return _normalize_mac(mac)
    return None


def _iptables_available() -> bool:
    return shutil.which("iptables") is not None


def _run_iptables(args: list[str]) -> None:
    try:
        subprocess.run(["iptables", *args], check=False, timeout=2)
    except Exception:  # pragma: no cover - defensive
        logger.exception("iptables command failed: %s", " ".join(args))


def _ensure_wlan0_drop_rule() -> None:
    if not _iptables_available():
        return
    check_args = [
        "-C",
        "FORWARD",
        "-i",
        "wlan0",
        "-o",
        "wlan1",
        "-j",
        "DROP",
    ]
    try:
        result = subprocess.run(
            ["iptables", *check_args],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except Exception:  # pragma: no cover - defensive
        logger.exception("iptables check failed for wlan0 drop rule")
        result = None
    if result is None or result.returncode != 0:
        _run_iptables(
            [
                "-A",
                "FORWARD",
                "-i",
                "wlan0",
                "-o",
                "wlan1",
                "-j",
                "DROP",
            ]
        )


def _load_allowlist() -> set[str]:
    path = _allowlist_path()
    if not path.exists():
        return set()
    try:
        content = path.read_text().splitlines()
    except OSError:  # pragma: no cover - defensive
        logger.exception("Unable to read public Wi-Fi allow list")
        return set()
    return {line.strip().lower() for line in content if line.strip()}


def _save_allowlist(macs: Iterable[str]) -> None:
    path = _allowlist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = sorted({m.lower() for m in macs if m})
    try:
        path.write_text("\n".join(lines) + ("\n" if lines else ""))
    except OSError:  # pragma: no cover - defensive
        logger.exception("Unable to write public Wi-Fi allow list")


def allow_mac(mac: str) -> None:
    mac = _normalize_mac(mac)
    if not mac:
        return
    allowlist = _load_allowlist()
    if mac not in allowlist:
        allowlist.add(mac)
        _save_allowlist(allowlist)
    if _iptables_available():
        _ensure_wlan0_drop_rule()
        check_args = [
            "-C",
            "FORWARD",
            "-i",
            "wlan0",
            "-m",
            "mac",
            "--mac-source",
            mac,
            "-j",
            "ACCEPT",
        ]
        try:
            result = subprocess.run(
                ["iptables", *check_args],
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            )
            exists = result.returncode == 0
        except Exception:  # pragma: no cover - defensive
            logger.exception("iptables check failed for %s", mac)
            exists = True
        if not exists:
            _run_iptables(
                [
                    "-I",
                    "FORWARD",
                    "1",
                    "-i",
                    "wlan0",
                    "-m",
                    "mac",
                    "--mac-source",
                    mac,
                    "-j",
                    "ACCEPT",
                ]
            )


def revoke_mac(mac: str) -> None:
    mac = _normalize_mac(mac)
    if not mac:
        return
    allowlist = _load_allowlist()
    if mac in allowlist:
        allowlist.remove(mac)
        _save_allowlist(allowlist)
    if _iptables_available():
        while True:
            try:
                result = subprocess.run(
                    [
                        "iptables",
                        "-D",
                        "FORWARD",
                        "-i",
                        "wlan0",
                        "-m",
                        "mac",
                        "--mac-source",
                        mac,
                        "-j",
                        "ACCEPT",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=2,
                )
            except Exception:  # pragma: no cover - defensive
                logger.exception("iptables delete failed for %s", mac)
                break
            if result.returncode != 0:
                break


def public_mode_lock_exists() -> bool:
    return _mode_lock().exists()


def grant_public_access(user, mac: str):
    from core.models import PublicWifiAccess

    mac = _normalize_mac(mac)
    if not mac:
        return None
    access, created = PublicWifiAccess.objects.get_or_create(
        user=user,
        mac_address=mac,
        defaults={"revoked_on": None},
    )
    if access.revoked_on is not None:
        access.revoked_on = None
        access.save(update_fields=["revoked_on", "updated_on"])
    allow_mac(mac)
    return access


def revoke_public_access(access) -> None:
    if access.revoked_on is None:
        access.revoked_on = timezone.now()
        access.save(update_fields=["revoked_on", "updated_on"])
    revoke_mac(access.mac_address)


def revoke_public_access_for_user(user) -> None:
    from core.models import PublicWifiAccess

    for access in PublicWifiAccess.objects.filter(user=user, revoked_on__isnull=True):
        revoke_public_access(access)
