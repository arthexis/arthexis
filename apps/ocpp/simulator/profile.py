"""Normalized charger profiles for reproducible simulator sessions."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


DEFAULT_PROFILE: dict[str, Any] = {
    "protocol": "ocpp1.6j",
    "target": {
        "url": None,
        "allow_insecure_ws": False,
        "timeout_seconds": 30.0,
    },
    "boot": {
        "vendor": "Arthexis",
        "model": "Gway Simulator",
        "serial": None,
        "firmware_version": None,
    },
    "behavior": {
        "authorization_timeout_seconds": 60.0,
        "heartbeat": True,
        "reconnect": True,
    },
    "clock": {
        "mode": "host",
        "offset_seconds": 0.0,
        "start_time": None,
    },
    "configuration": {},
}


class ChargerProfileSource(Protocol):
    """Source capable of producing one normalized charger-profile mapping."""

    def load(self) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class JsonChargerProfileSource:
    """Load a charger profile from an editable JSON file."""

    path: Path

    def load(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError(f"charger profile does not exist: {self.path}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"charger profile is not valid JSON: {self.path}") from exc
        if not isinstance(payload, dict):
            raise ValueError("charger profile root must be a JSON object")
        return payload


@dataclass(frozen=True)
class ChargerProfile:
    """Normalized portable description of the charger being simulated."""

    identity: str
    protocol: str = "ocpp1.6j"
    target: dict[str, Any] = field(default_factory=dict)
    boot: dict[str, Any] = field(default_factory=dict)
    behavior: dict[str, Any] = field(default_factory=dict)
    clock: dict[str, Any] = field(default_factory=dict)
    configuration: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any]) -> ChargerProfile:
        merged = _deep_merge(copy.deepcopy(DEFAULT_PROFILE), mapping)
        identity = str(merged.get("identity") or "").strip()
        if not identity:
            raise ValueError("charger profile requires identity")
        protocol = str(merged.get("protocol") or "").strip().lower()
        if protocol not in {"ocpp1.6", "ocpp1.6j"}:
            raise ValueError("charger profile protocol must be ocpp1.6j")

        target = _object(merged, "target")
        boot = _object(merged, "boot")
        behavior = _object(merged, "behavior")
        clock = _object(merged, "clock")
        configuration = _object(merged, "configuration")

        timeout = float(target.get("timeout_seconds", 30.0))
        if timeout <= 0:
            raise ValueError("target.timeout_seconds must be positive")
        target["timeout_seconds"] = timeout
        target["allow_insecure_ws"] = bool(target.get("allow_insecure_ws", False))

        authorization_timeout = float(
            behavior.get("authorization_timeout_seconds", 60.0)
        )
        if authorization_timeout <= 0:
            raise ValueError("behavior.authorization_timeout_seconds must be positive")
        behavior["authorization_timeout_seconds"] = authorization_timeout
        behavior["heartbeat"] = bool(behavior.get("heartbeat", True))
        behavior["reconnect"] = bool(behavior.get("reconnect", True))

        clock_mode = str(clock.get("mode") or "host").strip().lower()
        if clock_mode not in {"host", "offset", "fixed", "frozen", "advancing"}:
            raise ValueError(
                "clock.mode must be host, offset, fixed, frozen, or advancing"
            )
        clock["mode"] = clock_mode
        clock["offset_seconds"] = float(clock.get("offset_seconds", 0.0))
        if clock_mode in {"fixed", "frozen", "advancing"} and not clock.get(
            "start_time"
        ):
            raise ValueError(f"clock.start_time is required for {clock_mode} mode")

        return cls(
            identity=identity,
            protocol="ocpp1.6j",
            target=target,
            boot=boot,
            behavior=behavior,
            clock=clock,
            configuration=configuration,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity,
            "protocol": self.protocol,
            "target": copy.deepcopy(self.target),
            "boot": copy.deepcopy(self.boot),
            "behavior": copy.deepcopy(self.behavior),
            "clock": copy.deepcopy(self.clock),
            "configuration": copy.deepcopy(self.configuration),
        }


def load_profile(
    source: ChargerProfileSource | None,
    *,
    base: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
) -> ChargerProfile:
    """Resolve defaults, source data, and explicit overrides into one profile."""
    resolved = copy.deepcopy(DEFAULT_PROFILE)
    if base:
        resolved = _deep_merge(resolved, base)
    if source is not None:
        resolved = _deep_merge(resolved, source.load())
    if overrides:
        resolved = _deep_merge(resolved, overrides)
    return ChargerProfile.from_mapping(resolved)


def _object(mapping: dict[str, Any], name: str) -> dict[str, Any]:
    value = mapping.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"charger profile {name} must be a JSON object")
    return copy.deepcopy(value)


def _deep_merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = _deep_merge(copy.deepcopy(base[key]), value)
        else:
            base[key] = copy.deepcopy(value)
    return base
