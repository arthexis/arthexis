"""Transport-focused SDK for integrating external workers with Arthexis."""

from .client import ArthexisClient, ArthexisClientConfig
from .errors import (
    ArthexisSDKError,
    SDKHTTPError,
    SDKRetryExhausted,
    SDKWebSocketError,
)
from .models import (
    DeviceHeartbeatRequest,
    DeviceHeartbeatResponse,
    EventSubmissionRequest,
    EventSubmissionResponse,
    SessionStateResponse,
    StartSessionRequest,
    StopSessionRequest,
)
from .retry import RetryPolicy

__all__ = [
    "ArthexisClient",
    "ArthexisClientConfig",
    "ArthexisSDKError",
    "DeviceHeartbeatRequest",
    "DeviceHeartbeatResponse",
    "EventSubmissionRequest",
    "EventSubmissionResponse",
    "RetryPolicy",
    "SDKHTTPError",
    "SDKRetryExhausted",
    "SDKWebSocketError",
    "SessionStateResponse",
    "StartSessionRequest",
    "StopSessionRequest",
]
