# Arthexis SDK client (`arthexis.sdk`)

`arthexis.sdk` provides a minimal, transport-focused client for external tooling.
Business decisions remain authoritative in Arthexis server-side services.

## Available operations

- Start session (HTTP)
- Stop session (HTTP)
- Device heartbeat (HTTP)
- Event submission (HTTP)
- Event submission (WebSocket)

## Typed models

The SDK exposes typed dataclass models for requests and responses:

- `StartSessionRequest`, `StopSessionRequest`, `SessionStateResponse`
- `DeviceHeartbeatRequest`, `DeviceHeartbeatResponse`
- `EventSubmissionRequest`, `EventSubmissionResponse`

## Retry and errors

HTTP transport uses `RetryPolicy` for transient status codes and network errors.
Errors are explicit:

- `SDKHTTPError` for permanent HTTP failures
- `SDKRetryExhausted` when retries are exhausted
- `SDKWebSocketError` for WebSocket transport/decoding failures

## Example scripts

The following examples are pure Python and do not import Django or ORM models:

- `examples/sdk/session_cli.py`
- `examples/sdk/heartbeat_worker.py`
