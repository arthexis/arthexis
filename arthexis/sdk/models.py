"""Typed request and response models for Arthexis SDK operations."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(slots=True)
class StartSessionRequest:
    """Request payload for starting a charging session."""

    device_id: str
    connector_id: str
    id_tag: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StopSessionRequest:
    """Request payload for stopping a charging session."""

    session_id: str
    reason: str
    meter_stop_wh: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DeviceHeartbeatRequest:
    """Request payload for a heartbeat emitted by an external device."""

    device_id: str
    firmware_version: str
    status: str
    measured_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    metrics: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class EventSubmissionRequest:
    """Request payload for event submission over HTTP or WebSocket."""

    device_id: str
    event_type: str
    payload: dict[str, Any]
    occurred_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    correlation_id: str | None = None


@dataclass(slots=True)
class SessionStateResponse:
    """Response payload describing session state as decided by Arthexis."""

    accepted: bool
    session_id: str
    status: str
    message: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SessionStateResponse":
        """Build a typed session response from JSON payload."""

        return cls(
            accepted=bool(payload.get("accepted", False)),
            session_id=str(payload.get("session_id", "")),
            status=str(payload.get("status", "")),
            message=payload.get("message"),
        )


@dataclass(slots=True)
class DeviceHeartbeatResponse:
    """Response payload for heartbeat ingestion acknowledgement."""

    accepted: bool
    server_time: str
    next_heartbeat_seconds: int
    message: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DeviceHeartbeatResponse":
        """Build a typed heartbeat response from JSON payload."""

        return cls(
            accepted=bool(payload.get("accepted", False)),
            server_time=str(payload.get("server_time", "")),
            next_heartbeat_seconds=int(payload.get("next_heartbeat_seconds", 0)),
            message=payload.get("message"),
        )


@dataclass(slots=True)
class EventSubmissionResponse:
    """Response payload for submitted events."""

    accepted: bool
    event_id: str
    status: str
    message: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EventSubmissionResponse":
        """Build a typed event response from JSON payload."""

        return cls(
            accepted=bool(payload.get("accepted", False)),
            event_id=str(payload.get("event_id", "")),
            status=str(payload.get("status", "")),
            message=payload.get("message"),
        )


def as_payload(model: Any) -> dict[str, Any]:
    """Serialize dataclass-based request models into JSON-ready dicts."""

    return asdict(model)
