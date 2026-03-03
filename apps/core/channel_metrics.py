"""In-memory telemetry for websocket and channel health signals."""

from __future__ import annotations

from collections import Counter
import logging
from threading import Lock
from typing import Final

logger = logging.getLogger(__name__)

_LOCK: Final[Lock] = Lock()
_ACTIVE_WEBSOCKET_SESSIONS = 0
_COUNTERS: Counter[str] = Counter()


def websocket_connected(*, source: str) -> int:
    """Increment active websocket session count and return the new value."""

    global _ACTIVE_WEBSOCKET_SESSIONS
    with _LOCK:
        _ACTIVE_WEBSOCKET_SESSIONS += 1
        _COUNTERS[f"websocket_connected_total:{source}"] += 1
        _COUNTERS["websocket_connected_total"] += 1
        return _ACTIVE_WEBSOCKET_SESSIONS


def websocket_disconnected(*, source: str) -> int:
    """Decrement active websocket session count and return the new value."""

    global _ACTIVE_WEBSOCKET_SESSIONS
    with _LOCK:
        _ACTIVE_WEBSOCKET_SESSIONS = max(_ACTIVE_WEBSOCKET_SESSIONS - 1, 0)
        _COUNTERS[f"websocket_disconnected_total:{source}"] += 1
        _COUNTERS["websocket_disconnected_total"] += 1
        return _ACTIVE_WEBSOCKET_SESSIONS


def failed_reconnect(*, source: str, reason: str) -> None:
    """Record a failed reconnect attempt for the given websocket source."""

    with _LOCK:
        _COUNTERS[f"failed_reconnect_total:{source}"] += 1
        _COUNTERS[f"failed_reconnect_reason_total:{reason}"] += 1
        _COUNTERS["failed_reconnect_total"] += 1


def stale_state_evicted(*, source: str, count: int) -> None:
    """Record stale state evictions for periodic health reporting."""

    if count <= 0:
        return
    with _LOCK:
        metric_key = f"stale_state_evicted_total:{source}"
        _COUNTERS[metric_key] += count
        _COUNTERS["stale_state_evicted_total"] += count


def metrics_snapshot() -> dict[str, int]:
    """Return an immutable snapshot of current websocket counters."""

    with _LOCK:
        snapshot = dict(_COUNTERS)
        snapshot["websocket_active_sessions"] = _ACTIVE_WEBSOCKET_SESSIONS
        return snapshot


def emit_periodic_metrics() -> dict[str, int]:
    """Log periodic websocket/channel metrics and return the latest snapshot."""

    snapshot = metrics_snapshot()
    logger.info(
        "channel_layer.metrics",
        extra={"event": "channel_layer.metrics", **snapshot},
    )
    return snapshot
