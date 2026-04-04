"""Typed errors surfaced by the transport-focused Arthexis SDK client."""

from __future__ import annotations


class ArthexisSDKError(Exception):
    """Base error type for SDK failures."""


class SDKHTTPError(ArthexisSDKError):
    """Raised when an HTTP request fails permanently."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code
        self.message = message


class SDKRetryExhausted(ArthexisSDKError):
    """Raised when retry attempts are exhausted."""


class SDKWebSocketError(ArthexisSDKError):
    """Raised for WebSocket transport failures."""
