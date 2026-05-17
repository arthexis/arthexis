from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections.abc import Iterable
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from django.conf import settings

from apps.nodes.roles import node_is_control

USB_INVENTORY_NODE_FEATURE_SLUG = "usb-inventory"
USB_INVENTORY_COMMAND_TIMEOUT_SECONDS = 15
DEFAULT_CLAIMS_PATH = Path("/etc/arthexis-usb/claims.json")
DEFAULT_STATE_PATH = Path("/run/arthexis-usb/devices.json")
DEFAULT_KINDLE_MARKERS = ("documents", "system")


class UsbInventoryError(Exception):
    """Raised when USB inventory command execution or parsing fails."""


def claims_path() -> Path:
    return Path(getattr(settings, "USB_INVENTORY_CLAIMS_PATH", DEFAULT_CLAIMS_PATH))


def state_path() -> Path:
    return Path(getattr(settings, "USB_INVENTORY_STATE_PATH", DEFAULT_STATE_PATH))


def kindle_markers() -> tuple[str, ...]:
    markers = getattr(settings, "USB_INVENTORY_KINDLE_MARKERS", DEFAULT_KINDLE_MARKERS)
    return tuple(str(marker).strip("/") for marker in markers if str(marker).strip("/"))


def has_usb_inventory_tools() -> bool:
    """Return whether the Linux block-device inventory tools are available."""

    return bool(shutil.which("lsblk") and shutil.which("findmnt"))


def usb_inventory_available(*, node=None) -> bool:
    """Return whether the current node may own USB inventory state."""

    return bool(
        node is not None and node_is_control(node) and has_usb_inventory_tools()
    )


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(path.parent),
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        temp_path.replace(path)
        temp_path = None
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass


def run_json(command: list[str]) -> dict[str, Any]:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=USB_INVENTORY_COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise UsbInventoryError(
            f"{command[0]} timed out after {USB_INVENTORY_COMMAND_TIMEOUT_SECONDS}s"
        ) from exc
    if result.returncode != 0:
        raise UsbInventoryError(
            (result.stderr or result.stdout or "command failed").strip()
        )
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise UsbInventoryError(f"{command[0]} returned invalid JSON") from exc


def _flatten_lsblk(
    devices: Iterable[dict[str, Any]], *, parent_usb: bool = False
) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for device in devices:
        item = dict(device)
        children = item.pop("children", []) or []
        current_usb = parent_usb or _is_usb_device(item)
        item["_parent_usb"] = parent_usb
        flattened.append(item)
        flattened.extend(_flatten_lsblk(children, parent_usb=current_usb))
    return flattened


def _mount_index(findmnt_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    filesystems = findmnt_data.get("filesystems", [])
    if not isinstance(filesystems, list):
        return index
    for filesystem in filesystems:
        if not isinstance(filesystem, dict):
            continue
        source = str(filesystem.get("source") or "")
        target = str(filesystem.get("target") or "")
        if not source or not target:
            continue
        entry = {
            "source": source,
            "target": target,
            "fstype": filesystem.get("fstype") or "",
            "options": filesystem.get("options") or "",
        }
        index[source] = entry
        index[Path(source).name] = entry
    return index


def _device_mount(
    device: dict[str, Any], mounts: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    mountpoint = device.get("mountpoint")
    if mountpoint:
        return {"target": str(mountpoint)}
    for candidate in (device.get("path"), device.get("name"), device.get("kname")):
        if not candidate:
            continue
        match = mounts.get(str(candidate)) or mounts.get(Path(str(candidate)).name)
        if match:
            return match
    return {}


def _is_usb_device(device: dict[str, Any]) -> bool:
    if device.get("_parent_usb"):
        return True
    signals = {
        str(device.get("tran") or "").lower(),
        str(device.get("subsystems") or "").lower(),
    }
    if "usb" in signals or any("usb" in signal for signal in signals):
        return True
    if str(device.get("hotplug") or "").lower() in {"1", "true"}:
        return True
    if str(device.get("rm") or "").lower() in {"1", "true"}:
        return True
    return False


def has_kindle_shape(mountpoint: str | None) -> bool:
    if not mountpoint:
        return False
    root = Path(mountpoint)
    if not root.is_dir():
        return False
    markers = kindle_markers()
    return bool(markers) and all((root / marker).exists() for marker in markers)


def _normalize_claims(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        claims = raw.get("claims")
        if isinstance(claims, list):
            return [claim for claim in claims if isinstance(claim, dict)]
        normalized: list[dict[str, Any]] = []
        for role, claim in raw.items():
            if role in {"claims", "version"}:
                continue
            if isinstance(claim, dict):
                normalized.append({"role": role, **claim})
        return normalized
    if isinstance(raw, list):
        return [claim for claim in raw if isinstance(claim, dict)]
    return []


def _claim_role(claim: dict[str, Any]) -> str:
    return str(claim.get("role") or claim.get("name") or "").strip()


def _device_claim_roles(device: dict[str, Any]) -> set[str]:
    roles: set[str] = set()
    claimed_roles = device.get("claimed_roles")
    if isinstance(claimed_roles, (list, tuple, set)):
        for role in claimed_roles:
            normalized_role = str(role).strip()
            if normalized_role:
                roles.add(normalized_role)

    claims = device.get("claims")
    if not isinstance(claims, (list, tuple)):
        return roles
    for claim in claims:
        if isinstance(claim, dict):
            role = _claim_role(claim)
        else:
            role = str(claim).strip()
        if role:
            roles.add(role)
    return roles


def _device_candidate_paths(device: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    mountpoint = str(device.get("mountpoint") or "").strip()
    if mountpoint:
        paths.append(mountpoint)
    mountpoints = device.get("mountpoints")
    if isinstance(mountpoints, (list, tuple, set)):
        for mountpoint in mountpoints:
            path = str(mountpoint or "").strip()
            if path:
                paths.append(path)
    mounts = device.get("mounts")
    if isinstance(mounts, (list, tuple)):
        for mount in mounts:
            if not isinstance(mount, dict):
                continue
            path = str(mount.get("target") or "").strip()
            if path:
                paths.append(path)
    if not paths:
        path = str(device.get("path") or "").strip()
        if path:
            paths.append(path)
    return paths


def _match_text(expected: object, actual: object) -> bool:
    if expected in (None, ""):
        return True
    actual_text = str(actual or "")
    expected_text = str(expected)
    if any(char in expected_text for char in "*?["):
        return fnmatch(actual_text.lower(), expected_text.lower())
    return actual_text.lower() == expected_text.lower()


def _claim_match_fields(claim: dict[str, Any]) -> dict[str, Any]:
    match = claim.get("match")
    if isinstance(match, dict):
        return match
    return claim


def match_claim(device: dict[str, Any], claim: dict[str, Any]) -> bool:
    """Return whether a device satisfies one local claim rule."""

    fields = _claim_match_fields(claim)
    for key in (
        "name",
        "kname",
        "path",
        "model",
        "serial",
        "uuid",
        "partuuid",
        "label",
        "partlabel",
        "fstype",
        "vendor",
    ):
        if key in fields and not _match_text(fields[key], device.get(key)):
            return False

    requires_kindle_shape = fields.get("kindle") is True or fields.get("kindle_shape") is True
    if requires_kindle_shape and not device.get("kindle_shape"):
        return False

    required_paths = fields.get("mount_contains", [])
    if isinstance(required_paths, str):
        required_paths = [required_paths]
    mountpoint = device.get("mountpoint")
    for relative in required_paths or []:
        if not mountpoint:
            return False
        candidate = (Path(str(mountpoint)) / str(relative).strip("/")).resolve()
        try:
            if not candidate.is_relative_to(Path(str(mountpoint)).resolve()):
                return False
        except OSError:
            return False
        if not candidate.exists():
            return False
    return True


def _normalize_device(
    device: dict[str, Any], mounts: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    mount = _device_mount(device, mounts)
    mountpoint = str(mount.get("target") or "")
    normalized = {
        "name": device.get("name") or "",
        "kname": device.get("kname") or "",
        "path": device.get("path") or "",
        "type": device.get("type") or "",
        "tran": device.get("tran") or "",
        "model": device.get("model") or "",
        "serial": device.get("serial") or "",
        "vendor": device.get("vendor") or "",
        "uuid": device.get("uuid") or "",
        "partuuid": device.get("partuuid") or "",
        "label": device.get("label") or "",
        "partlabel": device.get("partlabel") or "",
        "fstype": device.get("fstype") or mount.get("fstype") or "",
        "mountpoint": mountpoint,
        "source": mount.get("source") or "",
        "kindle_shape": has_kindle_shape(mountpoint),
    }
    normalized["id"] = (
        normalized["uuid"]
        or normalized["partuuid"]
        or normalized["serial"]
        or normalized["path"]
        or normalized["name"]
    )
    return normalized


def inventory_devices() -> list[dict[str, Any]]:
    lsblk_data = run_json(
        [
            "lsblk",
            "--json",
            "--output",
            "NAME,KNAME,PATH,TYPE,TRAN,MODEL,SERIAL,VENDOR,UUID,PARTUUID,LABEL,PARTLABEL,FSTYPE,MOUNTPOINT,HOTPLUG,RM,SUBSYSTEMS",
        ]
    )
    findmnt_data = run_json(
        ["findmnt", "--json", "--output", "SOURCE,TARGET,FSTYPE,OPTIONS"]
    )
    mounts = _mount_index(findmnt_data)
    devices = []
    for device in _flatten_lsblk(lsblk_data.get("blockdevices", [])):
        if not isinstance(device, dict) or not _is_usb_device(device):
            continue
        normalized = _normalize_device(device, mounts)
        if normalized["type"] in {"disk", "part"}:
            devices.append(normalized)
    return devices


def refresh_inventory() -> dict[str, Any]:
    """Refresh and persist the local USB inventory snapshot."""

    claims = _normalize_claims(load_json(claims_path(), {}))
    devices = inventory_devices()
    for device in devices:
        device_claims = []
        for claim in claims:
            role = _claim_role(claim)
            if role and match_claim(device, claim):
                device_claims.append(role)
        device["claims"] = sorted(set(device_claims))

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "claims_path": str(claims_path()),
        "devices": devices,
    }
    atomic_write_json(state_path(), payload)
    return payload


def state_or_refresh(*, refresh: bool = False) -> dict[str, Any]:
    if refresh:
        return refresh_inventory()
    state = load_json(state_path(), {})
    if isinstance(state, dict) and isinstance(state.get("devices"), list):
        return state
    return refresh_inventory()


def claimed_paths(role: str, *, refresh: bool = False) -> list[str]:
    wanted = role.strip()
    if not wanted:
        return []
    state = state_or_refresh(refresh=refresh)
    paths = []
    for device in state.get("devices", []):
        if not isinstance(device, dict):
            continue
        if wanted not in _device_claim_roles(device):
            continue
        paths.extend(_device_candidate_paths(device))
    return sorted(set(paths))


def path_claims(path: str | Path, *, refresh: bool = False) -> list[str]:
    target = Path(path)
    try:
        resolved_target = target.resolve()
    except OSError:
        resolved_target = target
    state = state_or_refresh(refresh=refresh)
    claims: set[str] = set()
    for device in state.get("devices", []):
        if not isinstance(device, dict):
            continue
        for root in _device_candidate_paths(device):
            if not root:
                continue
            root_path = Path(str(root))
            try:
                if resolved_target.is_relative_to(root_path.resolve()):
                    claims.update(_device_claim_roles(device))
            except OSError:
                continue
    return sorted(claims)


__all__ = [
    "USB_INVENTORY_NODE_FEATURE_SLUG",
    "UsbInventoryError",
    "claimed_paths",
    "has_usb_inventory_tools",
    "inventory_devices",
    "match_claim",
    "path_claims",
    "refresh_inventory",
    "state_or_refresh",
    "usb_inventory_available",
]
