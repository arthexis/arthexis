from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec
from typing import Mapping

from apps.features.parameters import get_feature_parameter


OCPP_SIMULATOR_FEATURE_SLUG = "ocpp-simulator"
ARTHEXIS_BACKEND_PARAMETER_KEY = "arthexis_backend"
MOBILITY_HOUSE_BACKEND_PARAMETER_KEY = "mobilityhouse_backend"
ARTHEXIS_BACKEND = "arthexis"
MOBILITY_HOUSE_BACKEND = "mobilityhouse"


def _coerce_bool(value: object, *, default: bool = False) -> bool:
    """Coerce a loosely typed value into a boolean."""

    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "on", "enabled", "y"}


def _is_simulator_backend_parameter_enabled(parameter_key: str, *, default: bool) -> bool:
    """Return whether one simulator backend parameter is enabled."""

    value = get_feature_parameter(
        OCPP_SIMULATOR_FEATURE_SLUG,
        parameter_key,
        fallback="enabled" if default else "disabled",
    )
    return _coerce_bool(value, default=default)


def _coerce_float(value: object, *, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return parsed


def _coerce_int(value: object, *, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return parsed


def _coerce_text(value: object, *, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        trimmed = value.strip()
        return trimmed if trimmed else default
    return str(value).strip() or default


def _coerce_reconnect_slots(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        tokens = [token.strip() for token in value.split(",") if token.strip()]
        if not tokens:
            return None
        return ",".join(tokens)
    try:
        count = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if count < 0:
        return None
    return str(count)


def _safe_interval(value: object, *, default: float) -> float:
    parsed = _coerce_float(value, default=default)
    return default if parsed <= 0 else parsed


def _coerce_wireless_delay(value: object, *, default: float) -> float:
    parsed = _coerce_float(value, default=default)
    return 0.0 if parsed < 0 else parsed


def sanitize_simulator_params(params: Mapping[str, object]) -> dict[str, object]:
    """Drop sensitive credentials from runtime params while preserving shape."""

    sanitized = dict(params)
    for field in ("username", "password"):
        sanitized.pop(field, None)
    return sanitized


@dataclass(slots=True)
class NormalizedSimulatorParams:
    """Canonical simulator runtime input after parsing and validation."""

    host: str = "127.0.0.1"
    ws_port: int | None = 8000
    ws_scheme: str | None = None
    use_tls: bool | None = None
    allow_private_network: bool = False
    rfid: str = "FFFFFFFF"
    vin: str = ""
    cp_path: str = "CPX"
    serial_number: str = ""
    connector_id: int = 1
    duration: int = 600
    interval: float = 5.0
    meter_interval: float = 5.0
    pre_charge_delay: float = 0.0
    average_kwh: float = 60.0
    amperage: float = 90.0
    repeat: object = False
    start_delay: float = 0.0
    reconnect_slots: str | None = None
    demo_mode: bool = False
    threads: int | None = None
    daemon: bool = True
    name: str = "Simulator"
    username: str | None = None
    password: str | None = None
    cp_idx: int = 1
    delay: float | None = None

    @classmethod
    def from_mapping(
        cls, values: Mapping[str, object], *, cp_idx: int | None = None
    ) -> "NormalizedSimulatorParams":
        payload = dict(values)
        interval = _safe_interval(payload.get("interval"), default=5.0)
        meter_interval = payload.get("meter_interval")
        if meter_interval is None:
            meter_interval = interval

        parsed = cls(
            host=_coerce_text(payload.get("host"), default="127.0.0.1"),
            ws_port=_coerce_int(payload.get("ws_port"), default=8000),
            ws_scheme=_coerce_text(payload.get("ws_scheme"), default="") or None,
            use_tls=(
                _coerce_bool(payload.get("use_tls"), default=False)
                if payload.get("use_tls") is not None
                else None
            ),
            allow_private_network=_coerce_bool(payload.get("allow_private_network")),
            rfid=_coerce_text(payload.get("rfid"), default="FFFFFFFF"),
            vin=_coerce_text(payload.get("vin"), default=""),
            cp_path=_coerce_text(payload.get("cp_path"), default="CPX"),
            serial_number=_coerce_text(payload.get("serial_number"), default=""),
            connector_id=_coerce_int(payload.get("connector_id"), default=1),
            duration=_coerce_int(payload.get("duration"), default=600),
            interval=interval,
            meter_interval=_safe_interval(meter_interval, default=interval),
            pre_charge_delay=_coerce_wireless_delay(
                payload.get("pre_charge_delay"), default=0.0
            ),
            average_kwh=_safe_interval(payload.get("average_kwh"), default=60.0),
            amperage=_safe_interval(payload.get("amperage"), default=90.0),
            repeat=payload.get("repeat", False),
            start_delay=_coerce_wireless_delay(payload.get("start_delay", payload.get("delay")), default=0.0),
            reconnect_slots=_coerce_reconnect_slots(payload.get("reconnect_slots")),
            demo_mode=_coerce_bool(payload.get("demo_mode")),
            threads=_coerce_int(payload.get("threads"), default=0) or None,
            daemon=_coerce_bool(payload.get("daemon"), default=True),
            name=_coerce_text(payload.get("name"), default="Simulator"),
            username=_coerce_text(payload.get("username"), default="") or None,
            password=_coerce_text(payload.get("password"), default="") or None,
            cp_idx=_coerce_int(payload.get("cp_idx"), default=cp_idx or 1),
            delay=_coerce_wireless_delay(payload.get("delay"), default=0.0),
        )
        return parsed

    def to_simulate_kwargs(self) -> dict[str, object]:
        return {
            "host": self.host,
            "ws_port": self.ws_port,
            "ws_scheme": self.ws_scheme,
            "use_tls": self.use_tls,
            "allow_private_network": self.allow_private_network,
            "rfid": self.rfid,
            "vin": self.vin,
            "cp_path": self.cp_path,
            "serial_number": self.serial_number,
            "connector_id": self.connector_id,
            "duration": self.duration,
            "average_kwh": self.average_kwh,
            "amperage": self.amperage,
            "pre_charge_delay": self.pre_charge_delay,
            "repeat": self.repeat,
            "threads": self.threads,
            "daemon": self.daemon,
            "interval": self.interval,
            "meter_interval": self.meter_interval,
            "username": self.username,
            "password": self.password,
            "delay": self.delay,
            "start_delay": self.start_delay,
            "reconnect_slots": self.reconnect_slots,
            "demo_mode": self.demo_mode,
            "name": self.name,
        }

    def to_state_payload(self) -> dict[str, object]:
        payload = self.to_simulate_kwargs()
        payload["start_delay"] = self.start_delay
        payload["delay"] = self.delay if self.delay is not None else self.start_delay
        payload["cp_idx"] = self.cp_idx
        return payload


@dataclass(frozen=True, slots=True)
class SimulatorBackendSelection:
    """Decision payload for simulator backend selection."""

    use_mobility_house: bool
    backend: str
    reason: str
    feature_enabled: bool
    dependency_available: bool


def _normalize_backend_override(value: object) -> str | None:
    """Normalize user-provided backend preference to a supported identifier."""

    if value is None:
        return None
    candidate = str(value).strip().lower()
    if candidate in {ARTHEXIS_BACKEND, "legacy"}:
        return ARTHEXIS_BACKEND
    if candidate in {MOBILITY_HOUSE_BACKEND, "mobility_house", "v2"}:
        return MOBILITY_HOUSE_BACKEND
    return None


def get_simulator_backend_choices() -> tuple[tuple[str, str], ...]:
    """Return backend dropdown choices filtered by enabled feature parameters."""

    choices: list[tuple[str, str]] = []
    if _is_simulator_backend_parameter_enabled(
        ARTHEXIS_BACKEND_PARAMETER_KEY,
        default=True,
    ):
        choices.append((ARTHEXIS_BACKEND, ARTHEXIS_BACKEND))
    if _is_simulator_backend_parameter_enabled(
        MOBILITY_HOUSE_BACKEND_PARAMETER_KEY,
        default=False,
    ):
        choices.append((MOBILITY_HOUSE_BACKEND, MOBILITY_HOUSE_BACKEND))
    return tuple(choices)


def resolve_simulator_backend(
    *, cp_idx: int = 1, preferred_backend: str | None = None
) -> SimulatorBackendSelection:
    """Return whether to use the Mobility House backend, with reasoning."""

    arthexis_enabled = _is_simulator_backend_parameter_enabled(
        ARTHEXIS_BACKEND_PARAMETER_KEY,
        default=True,
    )
    mobility_house_enabled = _is_simulator_backend_parameter_enabled(
        MOBILITY_HOUSE_BACKEND_PARAMETER_KEY,
        default=False,
    )
    dependency_available = find_spec("ocpp") is not None
    backend_available = arthexis_enabled or (
        mobility_house_enabled and dependency_available
    )
    backend_override = _normalize_backend_override(preferred_backend)

    if backend_override == ARTHEXIS_BACKEND:
        if arthexis_enabled:
            return SimulatorBackendSelection(
                use_mobility_house=False,
                backend="legacy",
                reason="Arthexis backend selected from simulator controls.",
                feature_enabled=backend_available,
                dependency_available=dependency_available,
            )
        backend_override = None

    if backend_override == MOBILITY_HOUSE_BACKEND:
        if mobility_house_enabled and dependency_available:
            return SimulatorBackendSelection(
                use_mobility_house=True,
                backend="mobility_house",
                reason=(
                    "Mobility House backend selected from simulator controls. "
                    f"Using v2 backend for slot {cp_idx}."
                ),
                feature_enabled=True,
                dependency_available=True,
            )
        backend_override = None

    if mobility_house_enabled and dependency_available:
        return SimulatorBackendSelection(
            use_mobility_house=True,
            backend="mobility_house",
            reason=(
                "Mobility House runtime enabled and dependency available. "
                f'Using v2 backend for slot {cp_idx}.'
            ),
            feature_enabled=True,
            dependency_available=True,
        )

    if arthexis_enabled:
        if mobility_house_enabled and not dependency_available:
            reason = (
                "Mobility House runtime requires the optional 'ocpp' package. "
                "v2 backend unavailable; using legacy simulator."
            )
        elif not mobility_house_enabled:
            reason = (
                "Mobility House runtime is disabled via suite feature parameter. "
                "Using Arthexis backend."
            )
        else:
            reason = "Using Arthexis backend."
        return SimulatorBackendSelection(
            use_mobility_house=False,
            backend="legacy",
            reason=reason,
            feature_enabled=backend_available,
            dependency_available=dependency_available,
        )

    if mobility_house_enabled and not dependency_available:
        return SimulatorBackendSelection(
            use_mobility_house=False,
            backend="legacy",
            reason=(
                "Mobility House backend is enabled, but the optional 'ocpp' package "
                "is not installed. Install 'ocpp' or disable mobilityhouse_backend."
            ),
            feature_enabled=False,
            dependency_available=False,
        )

    return SimulatorBackendSelection(
        use_mobility_house=False,
        backend="legacy",
        reason=(
            "Simulator backends are disabled via suite feature parameters. "
            "Enable Arthexis and/or Mobility House backend options."
        ),
        feature_enabled=backend_available,
        dependency_available=dependency_available,
    )


def should_use_mobility_house_backend() -> tuple[bool, str]:
    """Return whether Mobility House should be used and why."""

    selection = resolve_simulator_backend()
    return selection.use_mobility_house, selection.reason


def normalize_simulator_params(
    params: Mapping[str, object], *, cp_idx: int = 1
) -> NormalizedSimulatorParams:
    """Build canonical simulator runtime params from a raw incoming mapping."""

    return NormalizedSimulatorParams.from_mapping(params, cp_idx=cp_idx)


def build_simulate_kwargs(
    params: Mapping[str, object], *, cp_idx: int = 1
) -> dict[str, object]:
    """Return backend-agnostic simulator keyword args from raw input."""

    return normalize_simulator_params(params, cp_idx=cp_idx).to_simulate_kwargs()


def build_normalized_simulator_params(
    params: Mapping[str, object], *, cp_idx: int = 1
) -> NormalizedSimulatorParams:
    """Return the canonical normalized parameter object."""

    return normalize_simulator_params(params, cp_idx=cp_idx)


def build_legacy_simulator_config(
    params: Mapping[str, object], *, cp_idx: int = 1
):
    """Build a legacy simulator config object from legacy-compatible inputs."""

    from apps.simulators.charge_point import SimulatorConfig

    normalized = build_normalized_simulator_params(params, cp_idx=cp_idx)
    kwargs = normalized.to_simulate_kwargs()
    kwargs["cp_idx"] = normalized.cp_idx
    return SimulatorConfig(**kwargs)


def build_mobility_house_simulator_config(
    params: Mapping[str, object], *, cp_idx: int = 1
):
    """Build a Mobility House simulator config object from legacy-compatible inputs."""

    from apps.simulators.evcs_mobilityhouse import MobilityHouseSimulatorConfig

    normalized = build_normalized_simulator_params(params, cp_idx=cp_idx)
    return MobilityHouseSimulatorConfig.from_payload(
        normalized.to_state_payload(),
        cp_idx=cp_idx,
    )


__all__ = [
    "OCPP_SIMULATOR_FEATURE_SLUG",
    "ARTHEXIS_BACKEND_PARAMETER_KEY",
    "MOBILITY_HOUSE_BACKEND_PARAMETER_KEY",
    "NormalizedSimulatorParams",
    "SimulatorBackendSelection",
    "sanitize_simulator_params",
    "get_simulator_backend_choices",
    "resolve_simulator_backend",
    "should_use_mobility_house_backend",
    "normalize_simulator_params",
    "build_simulate_kwargs",
    "build_normalized_simulator_params",
    "build_legacy_simulator_config",
    "build_mobility_house_simulator_config",
]
