"""Preset definitions for OCPP simulator runtime parameters."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

BASE_PRESET_KEY = "default"

SIMULATOR_PRESETS: dict[str, dict[str, Any]] = {
    BASE_PRESET_KEY: {
        "host": "127.0.0.1",
        "ws_port": 8000,
        "cp_path": "CP2",
        "serial_number": "CP2",
        "connector_id": 1,
        "rfid": "FFFFFFFF",
        "vin": "WP0ZZZ00000000000",
        "duration": 600,
        "interval": 5.0,
        "pre_charge_delay": 0.0,
        "average_kwh": 60.0,
        "amperage": 90.0,
        "repeat": False,
        "username": "",
        "password": "",
        "start_delay": 0.0,
        "reconnect_slots": None,
        "demo_mode": False,
        "meter_interval": 5.0,
        "allow_private_network": True,
        "ws_scheme": None,
        "use_tls": None,
    },
    "demo": {
        "duration": 180,
        "interval": 2.0,
        "average_kwh": 20.0,
        "amperage": 40.0,
        "demo_mode": True,
        "meter_interval": 2.0,
    },
    "longhaul": {
        "duration": 3600,
        "interval": 15.0,
        "average_kwh": 90.0,
        "amperage": 120.0,
        "meter_interval": 15.0,
    },
}


def get_simulator_preset_names() -> tuple[str, ...]:
    """Return available simulator preset names sorted alphabetically."""

    return tuple(sorted(SIMULATOR_PRESETS.keys()))


def get_simulator_preset(name: str) -> dict[str, Any]:
    """Return a merged preset payload for the given preset name."""

    normalized_name = name.strip().lower()
    if normalized_name not in SIMULATOR_PRESETS:
        raise ValueError(f"Unknown simulator preset '{name}'.")

    merged = deepcopy(SIMULATOR_PRESETS[BASE_PRESET_KEY])
    if normalized_name != BASE_PRESET_KEY:
        merged.update(SIMULATOR_PRESETS[normalized_name])
    return merged


__all__ = [
    "BASE_PRESET_KEY",
    "SIMULATOR_PRESETS",
    "get_simulator_preset",
    "get_simulator_preset_names",
]
