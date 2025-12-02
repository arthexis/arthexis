from __future__ import annotations

import subprocess
from datetime import datetime
from typing import Iterable

from django.utils import timezone

NMCLI_FIELDS: tuple[str, ...] = (
    "connection.id",
    "connection.uuid",
    "connection.type",
    "connection.interface-name",
    "connection.autoconnect",
    "connection.autoconnect-priority",
    "connection.metered",
    "connection.timestamp",
    "ipv4.addresses",
    "ipv4.method",
    "ipv4.gateway",
    "ipv4.dns",
    "ipv4.dhcp-client-id",
    "ipv4.dhcp-hostname",
    "ipv6.addresses",
    "ipv6.method",
    "ipv6.gateway",
    "ipv6.dns",
    "802-11-wireless.ssid",
    "802-11-wireless.mode",
    "802-11-wireless.band",
    "802-11-wireless.channel",
    "802-11-wireless.mac-address",
    "802-11-wireless-security.key-mgmt",
    "802-11-wireless-security.psk",
)

FIELD_MAP: dict[str, str] = {
    "connection.id": "connection_id",
    "connection.uuid": "uuid",
    "connection.type": "connection_type",
    "connection.interface-name": "interface_name",
    "connection.autoconnect": "autoconnect",
    "connection.autoconnect-priority": "priority",
    "connection.metered": "metered",
    "connection.timestamp": "last_modified_at",
    "ipv4.addresses": "ip4_address",
    "ipv4.method": "ip4_method",
    "ipv4.gateway": "ip4_gateway",
    "ipv4.dns": "ip4_dns",
    "ipv4.dhcp-client-id": "dhcp_client_id",
    "ipv4.dhcp-hostname": "dhcp_hostname",
    "ipv6.addresses": "ip6_address",
    "ipv6.method": "ip6_method",
    "ipv6.gateway": "ip6_gateway",
    "ipv6.dns": "ip6_dns",
    "802-11-wireless.ssid": "wireless_ssid",
    "802-11-wireless.mode": "wireless_mode",
    "802-11-wireless.band": "wireless_band",
    "802-11-wireless.channel": "wireless_channel",
    "802-11-wireless.mac-address": "mac_address",
    "802-11-wireless-security.key-mgmt": "security_type",
    "802-11-wireless-security.psk": "password",
}


class NMCLIScanError(RuntimeError):
    """Raised when nmcli cannot be executed or returns an unexpected error."""


def _run_nmcli(args: Iterable[str]) -> str:
    try:
        result = subprocess.run(
            ["nmcli", *args], capture_output=True, text=True, check=True
        )
    except FileNotFoundError as exc:  # pragma: no cover - external binary
        raise NMCLIScanError("nmcli is not available on this system.") from exc
    except subprocess.CalledProcessError as exc:  # pragma: no cover - external binary
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        details = stderr or stdout or str(exc)
        raise NMCLIScanError(details) from exc
    return result.stdout


def _parse_detail_output(output: str) -> dict[str, object]:
    data: dict[str, object] = {}
    for raw_line in output.splitlines():
        if ":" not in raw_line:
            continue
        key, value = raw_line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key not in FIELD_MAP:
            continue
        mapped_key = FIELD_MAP[key]
        data[mapped_key] = value

    autoconnect = str(data.get("autoconnect", "")).lower()
    data["autoconnect"] = autoconnect in {"yes", "true", "1"}

    try:
        data["priority"] = int(data.get("priority", ""))
    except (TypeError, ValueError):
        data["priority"] = None

    timestamp = data.get("last_modified_at")
    if timestamp:
        try:
            ts_value = float(str(timestamp))
            data["last_modified_at"] = timezone.make_aware(
                datetime.fromtimestamp(ts_value)
            )
        except (TypeError, ValueError, OSError, OverflowError):
            data["last_modified_at"] = None
    else:
        data["last_modified_at"] = None

    return data


def _fetch_connection_details(name: str) -> dict[str, object]:
    output = _run_nmcli(
        ["-m", "multiline", "-f", ",".join(NMCLI_FIELDS), "connection", "show", name]
    )
    details = _parse_detail_output(output)
    if "connection_id" not in details:
        details["connection_id"] = name
    return details


def scan_nmcli_connections() -> tuple[list[dict[str, object]], list[str]]:
    """Return parsed nmcli connection details and any non-fatal errors."""

    overview_output = _run_nmcli(["-t", "-f", "NAME", "connection", "show"])
    names = [line.strip() for line in overview_output.splitlines() if line.strip()]

    records: list[dict[str, object]] = []
    errors: list[str] = []

    for name in names:
        try:
            records.append(_fetch_connection_details(name))
        except NMCLIScanError as exc:
            errors.append(f"{name}: {exc}")

    return records, errors
