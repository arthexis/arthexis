"""Service helpers for controlling and inventorying Bluetooth devices."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Iterable

from django.utils import timezone

from .models import BluetoothAdapter, BluetoothDevice


class BluetoothCommandError(RuntimeError):
    """Raised when bluetoothctl execution fails."""


class BluetoothParseError(RuntimeError):
    """Raised when bluetoothctl output cannot be parsed reliably."""


TRUTHY = {"yes", "true", "on"}
_BLUETOOTHCTL_TIMEOUT_S = 15
_REGISTRATION_UPDATE_FIELDS = ["is_registered", "registered_at", "registered_by"]


def _run_bluetoothctl(args: Iterable[str]) -> str:
    """Run bluetoothctl and return stdout.

    :raises BluetoothCommandError: if bluetoothctl is unavailable or exits non-zero.
    """

    try:
        result = subprocess.run(
            ["bluetoothctl", *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=_BLUETOOTHCTL_TIMEOUT_S,
        )
    except FileNotFoundError as exc:  # pragma: no cover
        raise BluetoothCommandError("bluetoothctl is not installed.") from exc
    except subprocess.TimeoutExpired as exc:  # pragma: no cover
        raise BluetoothCommandError(
            f"bluetoothctl timed out after {_BLUETOOTHCTL_TIMEOUT_S}s."
        ) from exc
    except subprocess.CalledProcessError as exc:  # pragma: no cover
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        details = stderr or stdout or f"exit code {exc.returncode}"
        raise BluetoothCommandError(f"bluetoothctl command failed: {details}") from exc
    return result.stdout


def _parse_yes_no(raw: str) -> bool:
    """Parse bluetoothctl yes/no-ish values."""

    return raw.strip().lower() in TRUTHY


def get_adapter_state(adapter_name: str = "hci0") -> dict[str, str | bool]:
    """Fetch adapter state from bluetoothctl show output."""

    output = _run_bluetoothctl(["show", adapter_name])
    state: dict[str, str | bool] = {"name": adapter_name}
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("Controller "):
            parts = stripped.split()
            if len(parts) >= 2:
                state["address"] = parts[1]
            continue
        if not stripped or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        normalized = key.strip().lower()
        value = value.strip()
        if normalized == "powered":
            state["powered"] = _parse_yes_no(value)
        elif normalized == "discoverable":
            state["discoverable"] = _parse_yes_no(value)
        elif normalized == "pairable":
            state["pairable"] = _parse_yes_no(value)
        elif normalized == "name":
            state["alias"] = value
        elif normalized == "alias":
            state["alias"] = value
    if "powered" not in state:
        raise BluetoothParseError(
            "Could not parse adapter state from bluetoothctl show."
        )
    return state


def set_adapter_power(
    powered: bool, adapter_name: str = "hci0"
) -> dict[str, str | bool]:
    """Enable or disable Bluetooth adapter power and persist state."""

    cmd = "on" if powered else "off"
    _run_bluetoothctl(["select", adapter_name])
    _run_bluetoothctl(["power", cmd])
    state = get_adapter_state(adapter_name)
    BluetoothAdapter.objects.update_or_create(
        name=adapter_name,
        defaults={
            "powered": bool(state.get("powered", False)),
            "discoverable": bool(state.get("discoverable", False)),
            "pairable": bool(state.get("pairable", False)),
            "address": str(state.get("address", "")),
            "alias": str(state.get("alias", "")),
            "last_checked_at": timezone.now(),
        },
    )
    return state


def _parse_devices(devices_output: str) -> list[dict[str, str]]:
    """Parse bluetoothctl devices output lines into address/name pairs."""

    parsed: list[dict[str, str]] = []
    for line in devices_output.splitlines():
        stripped = line.strip()
        if not stripped.startswith("Device "):
            continue
        parts = stripped.split(" ", 2)
        if len(parts) < 3:
            continue
        parsed.append({"address": parts[1], "name": parts[2].strip()})
    return parsed


def _parse_device_info(output: str) -> dict[str, str | bool | int | list[str]]:
    """Parse bluetoothctl info output for one device."""

    details: dict[str, str | bool | int | list[str]] = {"uuids": []}
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("Device "):
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "name":
            details["name"] = value
        elif key == "alias":
            details["alias"] = value
        elif key == "icon":
            details["icon"] = value
        elif key == "paired":
            details["paired"] = _parse_yes_no(value)
        elif key == "trusted":
            details["trusted"] = _parse_yes_no(value)
        elif key == "blocked":
            details["blocked"] = _parse_yes_no(value)
        elif key == "connected":
            details["connected"] = _parse_yes_no(value)
        elif key == "rssi":
            try:
                details["rssi"] = int(value)
            except ValueError:
                details["rssi"] = None
        elif key.startswith("uuid"):
            details.setdefault("uuids", []).append(value)
    return details


def discover_and_sync_devices(
    adapter_name: str = "hci0", timeout_s: int = 4
) -> dict[str, int]:
    """Run a brief discovery scan and synchronize local Bluetooth inventory."""

    set_adapter_power(powered=True, adapter_name=adapter_name)
    _run_bluetoothctl(["scan", "on"])
    time.sleep(max(timeout_s, 0))
    _run_bluetoothctl(["scan", "off"])

    adapter, _ = BluetoothAdapter.objects.get_or_create(name=adapter_name)
    adapter_state = get_adapter_state(adapter_name)
    adapter.powered = bool(adapter_state.get("powered", False))
    adapter.discoverable = bool(adapter_state.get("discoverable", False))
    adapter.pairable = bool(adapter_state.get("pairable", False))
    adapter.address = str(adapter_state.get("address", ""))
    adapter.alias = str(adapter_state.get("alias", ""))
    adapter.last_checked_at = timezone.now()
    adapter.save()

    devices = _parse_devices(_run_bluetoothctl(["devices"]))
    now = timezone.now()
    created = 0
    updated = 0

    for item in devices:
        details = _parse_device_info(_run_bluetoothctl(["info", item["address"]]))
        obj, created_flag = BluetoothDevice.objects.update_or_create(
            address=item["address"],
            defaults={
                "adapter": adapter,
                "name": str(details.get("name") or item.get("name") or ""),
                "alias": str(details.get("alias") or ""),
                "icon": str(details.get("icon") or ""),
                "paired": bool(details.get("paired", False)),
                "trusted": bool(details.get("trusted", False)),
                "blocked": bool(details.get("blocked", False)),
                "connected": bool(details.get("connected", False)),
                "rssi": (
                    details.get("rssi")
                    if isinstance(details.get("rssi"), int)
                    else None
                ),
                "uuids": (
                    details.get("uuids")
                    if isinstance(details.get("uuids"), list)
                    else []
                ),
                "last_seen_at": now,
            },
        )
        if created_flag:
            created += 1
            obj.first_seen_at = now
            obj.save(update_fields=["first_seen_at"])
        else:
            updated += 1

    return {"created": created, "updated": updated, "count": len(devices)}


def register_device(address: str, user: object | None = None) -> BluetoothDevice:
    """Mark a known Bluetooth device as registered."""

    device = BluetoothDevice.objects.get(address=address)
    device.is_registered = True
    device.registered_at = timezone.now()
    if user is not None:
        device.registered_by = user
    device.save(update_fields=_REGISTRATION_UPDATE_FIELDS)
    return device


def unregister_device(address: str) -> BluetoothDevice:
    """Mark a known Bluetooth device as unregistered."""

    device = BluetoothDevice.objects.get(address=address)
    device.is_registered = False
    device.registered_at = None
    device.registered_by = None
    device.save(update_fields=_REGISTRATION_UPDATE_FIELDS)
    return device
