from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

from apps.sensors import usb_inventory

GWAY_NETWORK_DREAM_ENDPOINT = "gway-001"
HIGH_THROUGHPUT_TERMS = (
    "audio",
    "camera",
    "capture",
    "microphone",
    "opencv",
    "recording-device",
    "rpicam",
    "stream",
    "uvc",
    "v4l",
    "video",
    "webcam",
)


@dataclass(frozen=True)
class DreamModeDecision:
    allowed: bool
    networks_enabled: bool
    node: str
    reason: str
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    high_throughput_peripherals: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _identity_values(node: object | None) -> tuple[str, ...]:
    if node is None:
        return ()
    values = []
    for attribute in ("public_endpoint", "hostname", "network_hostname"):
        value = str(getattr(node, attribute, "") or "").strip().lower()
        if value:
            values.append(value)
    return tuple(values)


def node_allows_network_dream_mode(node: object | None) -> bool:
    return GWAY_NETWORK_DREAM_ENDPOINT in _identity_values(node)


def _iter_text_values(value: Any) -> Iterable[str]:
    if value is None:
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_text_values(item)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _iter_text_values(item)
        return
    text = str(value).strip()
    if text:
        yield text


def _peripheral_label(device: dict[str, Any]) -> str:
    for key in ("label", "model", "name", "path", "id"):
        value = str(device.get(key) or "").strip()
        if value:
            return value
    return "unknown USB peripheral"


def high_throughput_peripherals(
    devices: Iterable[dict[str, Any]], *, terms: Iterable[str] = HIGH_THROUGHPUT_TERMS
) -> tuple[str, ...]:
    normalized_terms = tuple(term.strip().lower() for term in terms if term.strip())
    matches = []
    for device in devices:
        if not isinstance(device, dict):
            continue
        device_text = " ".join(_iter_text_values(device)).lower()
        if any(term in device_text for term in normalized_terms):
            matches.append(_peripheral_label(device))
    return tuple(dict.fromkeys(matches))


def _load_high_throughput_peripherals(
    *,
    refresh_inventory: bool = False,
    inventory_devices: Iterable[dict[str, Any]] | None = None,
) -> tuple[tuple[str, ...], str]:
    if inventory_devices is not None:
        return high_throughput_peripherals(inventory_devices), ""
    try:
        state = usb_inventory.state_or_refresh(refresh=refresh_inventory)
    except Exception as exc:
        return (), f"USB inventory unavailable: {exc}"
    devices = state.get("devices") if isinstance(state, dict) else None
    if not isinstance(devices, list):
        return (), "USB inventory state does not contain a devices list."
    return high_throughput_peripherals(devices), ""


def evaluate_dream_mode(
    *,
    node: object | None,
    networks_enabled: bool,
    refresh_inventory: bool = False,
    inventory_devices: Iterable[dict[str, Any]] | None = None,
) -> DreamModeDecision:
    node_label = next(iter(_identity_values(node)), "")
    if not node_label:
        return DreamModeDecision(
            allowed=False,
            networks_enabled=networks_enabled,
            node="",
            reason="No local node is registered.",
            blockers=("local-node-missing",),
        )

    peripherals, inventory_error = _load_high_throughput_peripherals(
        refresh_inventory=refresh_inventory,
        inventory_devices=inventory_devices,
    )
    if networks_enabled:
        if not node_allows_network_dream_mode(node):
            return DreamModeDecision(
                allowed=False,
                networks_enabled=True,
                node=node_label,
                reason="Network-on dream mode is restricted to gway-001.",
                blockers=("network-on-not-allowed-for-node",),
                high_throughput_peripherals=peripherals,
            )
        if inventory_error:
            return DreamModeDecision(
                allowed=False,
                networks_enabled=True,
                node=node_label,
                reason=inventory_error,
                blockers=("high-throughput-inventory-unavailable",),
            )
        if peripherals:
            return DreamModeDecision(
                allowed=False,
                networks_enabled=True,
                node=node_label,
                reason="Disconnect high-throughput peripherals before network-on dream mode.",
                blockers=("high-throughput-peripherals-connected",),
                high_throughput_peripherals=peripherals,
            )
        return DreamModeDecision(
            allowed=True,
            networks_enabled=True,
            node=node_label,
            reason="gway-001 may keep networks enabled because no high-throughput peripherals are connected.",
        )

    warnings = ()
    if peripherals:
        warnings = ("high-throughput-peripherals-connected",)
    return DreamModeDecision(
        allowed=True,
        networks_enabled=False,
        node=node_label,
        reason="Network-off dream mode preserves the existing node behavior.",
        warnings=warnings,
        high_throughput_peripherals=peripherals,
    )


__all__ = [
    "DreamModeDecision",
    "GWAY_NETWORK_DREAM_ENDPOINT",
    "HIGH_THROUGHPUT_TERMS",
    "evaluate_dream_mode",
    "high_throughput_peripherals",
    "node_allows_network_dream_mode",
]
