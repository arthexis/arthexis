from __future__ import annotations

import httpx
import pytest
from websocket import WebSocketException

from arthexis.sdk import (
    ArthexisClient,
    ArthexisClientConfig,
    EventSubmissionRequest,
    RetryPolicy,
    SDKHTTPError,
    SDKRetryExhausted,
    SDKWebSocketError,
    StartSessionRequest,
)
from arthexis.sdk.transport import HTTPTransport, WebSocketTransport


class DummySocket:
    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.sent: list[str] = []

    def send(self, payload: str) -> None:
        self.sent.append(payload)

    def recv(self) -> str:
        return self.response_text

    def close(self) -> None:
        return None


class SendFailingSocket(DummySocket):
    def __init__(self, exc: Exception) -> None:
        super().__init__('{"accepted": true}')
        self.closed = False
        self.exc = exc

    def send(self, payload: str) -> None:
        self.sent.append(payload)
        raise self.exc

    def close(self) -> None:
        self.closed = True


def test_start_session_maps_typed_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/sdk/sessions/start"
        return httpx.Response(
            200,
            json={
                "accepted": True,
                "session_id": "session-123",
                "status": "active",
            },
        )

    http_client = httpx.Client(
        base_url="https://example.test",
        transport=httpx.MockTransport(handler),
    )
    client = ArthexisClient(
        ArthexisClientConfig(base_url="https://example.test"),
        http_transport=HTTPTransport(base_url="https://example.test", client=http_client),
    )

    response = client.start_session(
        StartSessionRequest(
            device_id="SIM-CP-1",
            connector_id="1",
            id_tag="RFID-1",
        )
    )

    assert response.accepted is True
    assert response.session_id == "session-123"
    assert response.status == "active"


def test_http_transport_retries_before_success() -> None:
    attempts = {"count": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] < 3:
            return httpx.Response(503, text="retry later")
        return httpx.Response(200, json={"accepted": True, "event_id": "evt-1", "status": "stored"})

    http_client = httpx.Client(
        base_url="https://example.test",
        transport=httpx.MockTransport(handler),
    )

    client = ArthexisClient(
        ArthexisClientConfig(
            base_url="https://example.test",
            retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=0),
        ),
        http_transport=HTTPTransport(
            base_url="https://example.test",
            retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=0),
            client=http_client,
        ),
    )

    response = client.submit_event_http(
        EventSubmissionRequest(
            device_id="SIM-CP-1",
            event_type="sdk.test",
            payload={"ok": True},
        )
    )

    assert attempts["count"] == 3
    assert response.event_id == "evt-1"


def test_http_transport_raises_non_retryable_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad request")

    transport = HTTPTransport(
        base_url="https://example.test",
        client=httpx.Client(
            base_url="https://example.test",
            transport=httpx.MockTransport(handler),
        ),
    )

    with pytest.raises(SDKHTTPError):
        transport.post_json("/sdk/events", {"x": 1})


def test_http_transport_raises_when_retries_exhausted() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="still failing")

    transport = HTTPTransport(
        base_url="https://example.test",
        retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=0),
        client=httpx.Client(
            base_url="https://example.test",
            transport=httpx.MockTransport(handler),
        ),
    )

    with pytest.raises(SDKRetryExhausted):
        transport.post_json("/sdk/events", {"x": 1})


def test_websocket_event_submission_uses_typed_response() -> None:
    socket = DummySocket('{"accepted": true, "event_id": "evt-2", "status": "queued"}')

    ws = WebSocketTransport(
        url="wss://example.test/sdk/events",
        websocket_factory=lambda *args, **kwargs: socket,
    )

    client = ArthexisClient(
        ArthexisClientConfig(
            base_url="https://example.test",
            events_ws_url="wss://example.test/sdk/events",
        ),
        websocket_transport=ws,
        http_transport=HTTPTransport(
            base_url="https://example.test",
            client=httpx.Client(
                base_url="https://example.test",
                transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
            ),
        ),
    )

    response = client.submit_event_websocket(
        EventSubmissionRequest(
            device_id="SIM-CP-1",
            event_type="sdk.ws",
            payload={"sample": True},
        )
    )

    assert response.accepted is True
    assert response.event_id == "evt-2"
    assert response.status == "queued"
    assert socket.sent


def test_websocket_transport_closes_socket_on_send_failure() -> None:
    socket = SendFailingSocket(WebSocketException("send failed"))
    ws = WebSocketTransport(
        url="wss://example.test/sdk/events",
        websocket_factory=lambda *args, **kwargs: socket,
    )

    with pytest.raises(SDKWebSocketError):
        ws.send_json({"x": 1})

    assert socket.closed is True


def test_websocket_transport_wraps_oserror_as_sdk_error() -> None:
    ws = WebSocketTransport(
        url="wss://example.test/sdk/events",
        websocket_factory=lambda *args, **kwargs: (_ for _ in ()).throw(OSError("dns down")),
    )

    with pytest.raises(SDKWebSocketError, match="dns down"):
        ws.send_json({"x": 1})
