"""Low-level HTTP and WebSocket transports with retry semantics."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx
from websocket import WebSocketException, create_connection

from .errors import SDKHTTPError, SDKRetryExhausted, SDKWebSocketError
from .retry import RetryPolicy

WebSocketFactory = Callable[..., Any]


@dataclass(slots=True)
class HTTPTransport:
    """HTTP transport wrapper that applies retry policy to transient failures."""

    base_url: str
    api_key: str | None = None
    timeout_seconds: float = 10.0
    retry_policy: RetryPolicy | None = None
    client: httpx.Client | None = None

    def __post_init__(self) -> None:
        self.retry_policy = self.retry_policy or RetryPolicy()

    def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST JSON payload and return decoded JSON response."""

        attempts = self.retry_policy.max_attempts
        if attempts < 1:
            raise ValueError("retry policy max_attempts must be >= 1")

        for attempt in range(1, attempts + 1):
            try:
                response = self._client().post(path, json=payload)
            except httpx.HTTPError as exc:
                if attempt >= attempts:
                    raise SDKRetryExhausted(
                        f"HTTP request failed after {attempts} attempts"
                    ) from exc
                time.sleep(self.retry_policy.delay_for_attempt(attempt))
                continue

            if 200 <= response.status_code < 300:
                return response.json()

            if self.retry_policy.should_retry_status(response.status_code):
                if attempt >= attempts:
                    raise SDKRetryExhausted(
                        f"HTTP request exhausted retries with status {response.status_code}"
                    )
                time.sleep(self.retry_policy.delay_for_attempt(attempt))
                continue

            raise SDKHTTPError(response.status_code, response.text)

    def _client(self) -> httpx.Client:
        if self.client is not None:
            return self.client

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        self.client = httpx.Client(
            base_url=self.base_url,
            headers=headers,
            follow_redirects=True,
            timeout=self.timeout_seconds,
        )
        return self.client


@dataclass(slots=True)
class WebSocketTransport:
    """WebSocket transport for event submission."""

    url: str
    api_key: str | None = None
    timeout_seconds: float = 10.0
    websocket_factory: WebSocketFactory = create_connection

    def send_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send JSON message and wait for a JSON response."""

        headers = []
        if self.api_key:
            headers.append(f"Authorization: Bearer {self.api_key}")

        try:
            socket = self.websocket_factory(
                self.url,
                timeout=self.timeout_seconds,
                header=headers,
            )
            try:
                socket.send(json.dumps(payload))
                raw = socket.recv()
            finally:
                socket.close()
        except (OSError, WebSocketException) as exc:
            raise SDKWebSocketError(str(exc)) from exc

        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SDKWebSocketError("WebSocket response was not valid JSON") from exc

        if not isinstance(parsed, dict):
            raise SDKWebSocketError("WebSocket response JSON must be an object")

        return parsed
