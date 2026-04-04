"""Transport-focused Arthexis SDK client."""

from __future__ import annotations

from dataclasses import dataclass

from .models import (
    DeviceHeartbeatRequest,
    DeviceHeartbeatResponse,
    EventSubmissionRequest,
    EventSubmissionResponse,
    SessionStateResponse,
    StartSessionRequest,
    StopSessionRequest,
    as_payload,
)
from .retry import RetryPolicy
from .transport import HTTPTransport, WebSocketTransport


@dataclass(slots=True)
class ArthexisClientConfig:
    """Configuration for SDK client endpoints and retry behavior."""

    base_url: str
    api_key: str | None = None
    timeout_seconds: float = 10.0
    retry_policy: RetryPolicy | None = None
    session_start_path: str = "/sdk/sessions/start"
    session_stop_path: str = "/sdk/sessions/stop"
    heartbeat_path: str = "/sdk/devices/heartbeat"
    events_http_path: str = "/sdk/events"
    events_ws_url: str | None = None


class ArthexisClient:
    """Minimal transport client that delegates authority to Arthexis services."""

    def __init__(
        self,
        config: ArthexisClientConfig,
        http_transport: HTTPTransport | None = None,
        websocket_transport: WebSocketTransport | None = None,
    ) -> None:
        self.config = config
        self.http_transport = http_transport or HTTPTransport(
            base_url=config.base_url,
            api_key=config.api_key,
            timeout_seconds=config.timeout_seconds,
            retry_policy=config.retry_policy,
        )
        self.websocket_transport = websocket_transport

    def start_session(self, request: StartSessionRequest) -> SessionStateResponse:
        payload = self.http_transport.post_json(
            self.config.session_start_path,
            as_payload(request),
        )
        return SessionStateResponse.from_dict(payload)

    def stop_session(self, request: StopSessionRequest) -> SessionStateResponse:
        payload = self.http_transport.post_json(
            self.config.session_stop_path,
            as_payload(request),
        )
        return SessionStateResponse.from_dict(payload)

    def device_heartbeat(
        self,
        request: DeviceHeartbeatRequest,
    ) -> DeviceHeartbeatResponse:
        payload = self.http_transport.post_json(
            self.config.heartbeat_path,
            as_payload(request),
        )
        return DeviceHeartbeatResponse.from_dict(payload)

    def submit_event_http(
        self,
        request: EventSubmissionRequest,
    ) -> EventSubmissionResponse:
        payload = self.http_transport.post_json(
            self.config.events_http_path,
            as_payload(request),
        )
        return EventSubmissionResponse.from_dict(payload)

    def submit_event_websocket(
        self,
        request: EventSubmissionRequest,
    ) -> EventSubmissionResponse:
        transport = self._resolve_websocket_transport()
        payload = transport.send_json(as_payload(request))
        return EventSubmissionResponse.from_dict(payload)

    def _resolve_websocket_transport(self) -> WebSocketTransport:
        if self.websocket_transport is not None:
            return self.websocket_transport

        if not self.config.events_ws_url:
            raise ValueError("events_ws_url must be configured for websocket submissions")

        self.websocket_transport = WebSocketTransport(
            url=self.config.events_ws_url,
            api_key=self.config.api_key,
            timeout_seconds=self.config.timeout_seconds,
        )
        return self.websocket_transport
