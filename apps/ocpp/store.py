"""In-memory store for OCPP data with file backed logs."""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import threading
import heapq
import itertools
from typing import Iterable, Iterator

from django.conf import settings
from redis import Redis
from redis.exceptions import RedisError

from apps.loggers.paths import select_log_dir

IDENTITY_SEPARATOR = "#"
AGGREGATE_SLUG = "all"
PENDING_SLUG = "pending"

MAX_CONNECTIONS_PER_IP = 2

_STATE_REDIS: Redis | None = None
_STATE_REDIS_URL = getattr(settings, "OCPP_STATE_REDIS_URL", "")
_PENDING_TTL = int(getattr(settings, "OCPP_PENDING_CALL_TTL", 1800) or 1800)
_IP_CONNECTION_TTL = 3600

def _state_redis() -> Redis | None:
    global _STATE_REDIS
    if not _STATE_REDIS_URL:
        return None
    if _STATE_REDIS is None:
        try:
            _STATE_REDIS = Redis.from_url(_STATE_REDIS_URL, decode_responses=True)
        except Exception:  # pragma: no cover - best effort fallback
            _STATE_REDIS = None
    return _STATE_REDIS

connections: dict[str, object] = {}
transactions: dict[str, object] = {}
# Maximum number of recent log entries to keep in memory per identity.
MAX_IN_MEMORY_LOG_ENTRIES = 1000

logs: dict[str, dict[str, deque[str]]] = {"charger": {}, "simulator": {}}
# store per charger session logs before they are flushed to disk
history: dict[str, dict[str, object]] = {}
simulators = {}
ip_connections: dict[str, set[object]] = {}
pending_calls: dict[str, dict[str, object]] = {}
_pending_call_events: dict[str, threading.Event] = {}
_pending_call_results: dict[str, dict[str, object]] = {}
_pending_call_lock = threading.Lock()
_pending_call_handles: dict[str, asyncio.TimerHandle] = {}
triggered_followups: dict[str, list[dict[str, object]]] = {}
monitoring_report_requests: dict[int, dict[str, object]] = {}
_monitoring_report_lock = threading.Lock()
transaction_requests: dict[str, dict[str, object]] = {}
_transaction_requests_by_connector: dict[str, set[str]] = {}
_transaction_requests_by_transaction: dict[str, set[str]] = {}
_transaction_requests_lock = threading.Lock()
billing_updates: deque[dict[str, object]] = deque(maxlen=1000)
ev_charging_needs: deque[dict[str, object]] = deque(maxlen=500)
ev_charging_schedules: deque[dict[str, object]] = deque(maxlen=500)
planner_notifications: deque[dict[str, object]] = deque(maxlen=500)
observability_events: deque[dict[str, object]] = deque(maxlen=1000)
transaction_events: deque[dict[str, object]] = deque(maxlen=1000)
connector_release_notifications: deque[dict[str, object]] = deque(maxlen=500)
monitoring_reports: deque[dict[str, object]] = deque(maxlen=1000)
display_message_compliance: dict[str, list[dict[str, object]]] = {}
charging_profile_reports: dict[str, dict[int, dict[str, object]]] = {}

# mapping of charger id / cp_path to friendly names used for log files
log_names: dict[str, dict[str, str]] = {"charger": {}, "simulator": {}}

BASE_DIR = Path(__file__).resolve().parents[2]
LOG_DIR = select_log_dir(BASE_DIR)
SESSION_DIR = LOG_DIR / "sessions"
SESSION_DIR.mkdir(exist_ok=True)
LOCK_DIR = BASE_DIR / ".locks"
LOCK_DIR.mkdir(exist_ok=True)
SESSION_LOCK = LOCK_DIR / "charging.lck"
_lock_task: asyncio.Task | None = None

_scheduler_loop: asyncio.AbstractEventLoop | None = None
_scheduler_thread: threading.Thread | None = None
_scheduler_lock = threading.Lock()

SESSION_LOG_BUFFER_LIMIT = 16


@dataclass(frozen=True)
class LogEntry:
    """Structured log entry returned by :func:`iter_log_entries`."""

    timestamp: datetime
    text: str


def connector_slug(value: int | str | None) -> str:
    """Return the canonical slug for a connector value."""

    if value in (None, "", AGGREGATE_SLUG):
        return AGGREGATE_SLUG
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(value)


def identity_key(serial: str, connector: int | str | None) -> str:
    """Return the identity key used for in-memory store lookups."""

    return f"{serial}{IDENTITY_SEPARATOR}{connector_slug(connector)}"


def _normalize_transaction_id(value: object | None) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _transaction_connector_key(charger_id: str | None, connector: int | str | None) -> str | None:
    if not charger_id:
        return None
    return identity_key(charger_id, connector)


def _remove_transaction_index_entry(
    mapping: dict[str, set[str]], key: str | None, message_id: str
) -> None:
    if not key:
        return
    entries = mapping.get(key)
    if not entries:
        return
    entries.discard(message_id)
    if not entries:
        mapping.pop(key, None)


def _add_transaction_index_entry(
    mapping: dict[str, set[str]], key: str | None, message_id: str
) -> None:
    if not key:
        return
    mapping.setdefault(key, set()).add(message_id)


def register_transaction_request(message_id: str, metadata: dict[str, object]) -> None:
    """Register a transaction-related request for later reconciliation."""

    entry = dict(metadata)
    entry.setdefault("status", "requested")
    entry.setdefault("status_at", datetime.now(timezone.utc))
    connector_key = _transaction_connector_key(
        str(entry.get("charger_id") or ""), entry.get("connector_id")
    )
    transaction_key = _normalize_transaction_id(
        entry.get("transaction_id") or entry.get("ocpp_transaction_id")
    )
    with _transaction_requests_lock:
        transaction_requests[message_id] = entry
        _add_transaction_index_entry(
            _transaction_requests_by_connector, connector_key, message_id
        )
        _add_transaction_index_entry(
            _transaction_requests_by_transaction, transaction_key, message_id
        )


def record_display_message_compliance(
    charger_id: str | None,
    *,
    request_id: int | None,
    tbc: bool,
    messages: list[dict[str, object]],
    received_at: datetime,
) -> None:
    """Track NotifyDisplayMessages payloads for compliance reporting."""

    if not charger_id:
        return
    record = {
        "charger_id": charger_id,
        "request_id": request_id,
        "tbc": tbc,
        "messages": messages,
        "received_at": received_at,
    }
    display_message_compliance.setdefault(charger_id, []).append(record)


def clear_display_message_compliance() -> None:
    """Clear cached NotifyDisplayMessages compliance data (test helper)."""

    display_message_compliance.clear()


def record_reported_charging_profile(
    charger_id: str | None,
    *,
    request_id: int | None,
    evse_id: int | str | None,
    profile_id: int | None,
) -> None:
    """Track profiles reported during a ReportChargingProfiles sequence."""

    if not charger_id or profile_id is None:
        return

    request_key = int(request_id) if request_id is not None else -1
    connector_key = connector_slug(evse_id)

    entry = charging_profile_reports.setdefault(charger_id, {}).setdefault(
        request_key, {"reported": {}}
    )
    entry["reported"].setdefault(connector_key, set()).add(profile_id)


def consume_reported_charging_profiles(
    charger_id: str | None, *, request_id: int | None
) -> dict[str, object] | None:
    """Pop recorded ReportChargingProfiles entries for the request."""

    if not charger_id:
        return None

    request_key = int(request_id) if request_id is not None else -1
    entries = charging_profile_reports.get(charger_id)
    if entries is None:
        return None

    record = entries.pop(request_key, None)
    if not entries:
        charging_profile_reports.pop(charger_id, None)

    if record is None:
        return None

    reported = record.get("reported") or {}
    normalized = {
        key: set(value) if isinstance(value, set) else set()
        for key, value in reported.items()
    }
    return {"reported": normalized}


def record_ev_charging_needs(
    charger_id: str | None,
    *,
    connector_id: int | str | None,
    evse_id: int,
    requested_energy: int | None,
    departure_time: datetime | None,
    charging_needs: dict[str, object] | None,
    received_at: datetime,
) -> None:
    """Track EV charging needs so schedulers can prioritize sessions."""

    if not charger_id:
        return

    record = {
        "charger_id": charger_id,
        "connector_id": connector_slug(connector_id),
        "evse_id": evse_id,
        "requested_energy": requested_energy,
        "departure_time": departure_time,
        "charging_needs": dict(charging_needs or {}),
        "received_at": received_at,
    }
    ev_charging_needs.append(record)


def record_ev_charging_schedule(
    charger_id: str | None,
    *,
    connector_id: int | str | None,
    evse_id: int,
    timebase: datetime | None,
    charging_schedule: dict[str, object] | None,
    received_at: datetime,
) -> None:
    """Track EV charging schedules so planners can synchronize demand."""

    if not charger_id or charging_schedule is None:
        return

    ev_charging_schedules.append(
        {
            "charger_id": charger_id,
            "connector_id": connector_slug(connector_id),
            "evse_id": evse_id,
            "timebase": timebase,
            "charging_schedule": dict(charging_schedule),
            "received_at": received_at,
        }
    )


def record_monitoring_report(
    charger_id: str | None,
    *,
    request_id: int | None,
    seq_no: int | None,
    generated_at: datetime | None,
    tbc: bool,
    component_name: str,
    component_instance: str,
    variable_name: str,
    variable_instance: str,
    monitoring_id: int,
    severity: int | None,
    monitor_type: str,
    threshold: str,
    is_transaction: bool,
    evse_id: int | None,
    connector_id: int | str | None,
    received_at: datetime,
) -> None:
    """Queue a normalized monitoring report entry for analytics pipelines."""

    if not charger_id:
        return

    monitoring_reports.append(
        {
            "charger_id": charger_id,
            "request_id": request_id,
            "seq_no": seq_no,
            "generated_at": generated_at,
            "tbc": tbc,
            "component_name": component_name,
            "component_instance": component_instance,
            "variable_name": variable_name,
            "variable_instance": variable_instance,
            "monitoring_id": monitoring_id,
            "severity": severity,
            "monitor_type": monitor_type,
            "threshold": threshold,
            "is_transaction": is_transaction,
            "evse_id": evse_id,
            "connector_id": connector_slug(connector_id),
            "received_at": received_at,
        }
    )


def forward_ev_charging_schedule(schedule: dict[str, object]) -> None:
    """Queue a normalized EV charging schedule for downstream planners."""

    planner_notifications.append(dict(schedule))


def forward_event_to_observability(event: dict[str, object]) -> None:
    """Queue a normalized NotifyEvent payload for observability pipelines."""

    observability_events.append(dict(event))


def record_transaction_event(event: dict[str, object]) -> None:
    """Queue a normalized TransactionEvent payload for downstream handlers."""

    transaction_events.append(dict(event))


def forward_connector_release(notification: dict[str, object]) -> None:
    """Queue a connector release notification for reservation workflows."""

    connector_release_notifications.append(dict(notification))


def update_transaction_request(
    message_id: str,
    *,
    status: str | None = None,
    connector_id: int | str | None = None,
    transaction_id: str | int | None = None,
    ocpp_transaction_id: str | int | None = None,
) -> dict[str, object] | None:
    """Update metadata for a tracked transaction request."""

    with _transaction_requests_lock:
        entry = transaction_requests.get(message_id)
        if not entry:
            return None
        if status:
            entry["status"] = status
            entry["status_at"] = datetime.now(timezone.utc)
        if connector_id is not None and connector_slug(entry.get("connector_id")) != connector_slug(
            connector_id
        ):
            old_key = _transaction_connector_key(
                str(entry.get("charger_id") or ""), entry.get("connector_id")
            )
            new_key = _transaction_connector_key(
                str(entry.get("charger_id") or ""), connector_id
            )
            _remove_transaction_index_entry(
                _transaction_requests_by_connector, old_key, message_id
            )
            _add_transaction_index_entry(
                _transaction_requests_by_connector, new_key, message_id
            )
            entry["connector_id"] = connector_id
        if transaction_id is not None or ocpp_transaction_id is not None:
            old_tx_key = _normalize_transaction_id(
                entry.get("transaction_id") or entry.get("ocpp_transaction_id")
            )
            new_tx_key = _normalize_transaction_id(
                transaction_id if transaction_id is not None else ocpp_transaction_id
            )
            if new_tx_key and new_tx_key != old_tx_key:
                _remove_transaction_index_entry(
                    _transaction_requests_by_transaction, old_tx_key, message_id
                )
                _add_transaction_index_entry(
                    _transaction_requests_by_transaction, new_tx_key, message_id
                )
                entry["transaction_id"] = new_tx_key
        return dict(entry)


def find_transaction_requests(
    *,
    charger_id: str,
    connector_id: int | str | None = None,
    transaction_id: str | int | None = None,
    action: str | None = None,
    statuses: set[str] | None = None,
) -> list[tuple[str, dict[str, object]]]:
    """Return tracked transaction requests matching the supplied filters."""

    connector_key = _transaction_connector_key(charger_id, connector_id)
    transaction_key = _normalize_transaction_id(transaction_id)
    candidates: set[str] = set()
    with _transaction_requests_lock:
        if transaction_key:
            candidates.update(
                _transaction_requests_by_transaction.get(transaction_key, set())
            )
        if connector_key:
            candidates.update(
                _transaction_requests_by_connector.get(connector_key, set())
            )
        results: list[tuple[str, dict[str, object]]] = []
        for message_id in candidates:
            entry = transaction_requests.get(message_id)
            if not entry:
                continue
            if entry.get("charger_id") != charger_id:
                continue
            if connector_id is not None and connector_slug(entry.get("connector_id")) != connector_slug(
                connector_id
            ):
                continue
            if action and entry.get("action") != action:
                continue
            if statuses and entry.get("status") not in statuses:
                continue
            results.append((message_id, dict(entry)))
    results.sort(
        key=lambda item: item[1].get("requested_at")
        or item[1].get("status_at")
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return results


def mark_transaction_requests(
    *,
    charger_id: str,
    connector_id: int | str | None = None,
    transaction_id: str | int | None = None,
    actions: Iterable[str] | None = None,
    statuses: set[str] | None = None,
    status: str,
) -> list[dict[str, object]]:
    """Update matching transaction requests and return the updated entries."""

    actions_set = set(actions or [])
    matches = find_transaction_requests(
        charger_id=charger_id,
        connector_id=connector_id,
        transaction_id=transaction_id,
    )
    updated: list[dict[str, object]] = []
    for message_id, entry in matches:
        if actions_set and entry.get("action") not in actions_set:
            continue
        if statuses and entry.get("status") not in statuses:
            continue
        update = update_transaction_request(
            message_id,
            status=status,
            connector_id=connector_id,
            transaction_id=transaction_id,
        )
        if update:
            updated.append(update)
    return updated


def forward_cost_update_to_billing(update: dict[str, object]) -> None:
    """Queue a cost update payload for downstream billing handlers."""

    billing_updates.append(dict(update))


def _connection_token(consumer: object) -> str:
    token = getattr(consumer, "_ocpp_state_token", None)
    if token:
        return token
    token = getattr(consumer, "channel_name", None) or f"consumer-{id(consumer)}"
    try:
        setattr(consumer, "_ocpp_state_token", token)
    except Exception:  # pragma: no cover - best effort
        pass
    return token


def _redis_ip_key(ip: str) -> str:
    return f"ocpp:ip-connection:{ip}"


def _register_ip_connection_redis(ip: str, consumer: object) -> bool | None:
    client = _state_redis()
    if not client:
        return None
    key = _redis_ip_key(ip)
    token = _connection_token(consumer)
    try:
        pipe = client.pipeline()
        pipe.sadd(key, token)
        pipe.expire(key, _IP_CONNECTION_TTL)
        pipe.scard(key)
        added, _expired, count = pipe.execute()
        if count > MAX_CONNECTIONS_PER_IP and added:
            client.srem(key, token)
            return False
        return count <= MAX_CONNECTIONS_PER_IP
    except RedisError:
        return None


def _release_ip_connection_redis(ip: str, consumer: object) -> None:
    client = _state_redis()
    if not client:
        return
    key = _redis_ip_key(ip)
    token = _connection_token(consumer)
    try:
        client.srem(key, token)
    except RedisError:
        return


def register_ip_connection(ip: str | None, consumer: object) -> bool:
    """Track a websocket connection for the provided client IP."""

    if not ip:
        return True
    allowed = _register_ip_connection_redis(ip, consumer)
    if allowed is False:
        return False
    conns = ip_connections.setdefault(ip, set())
    if consumer in conns:
        return True
    if len(conns) >= MAX_CONNECTIONS_PER_IP:
        if allowed:
            _release_ip_connection_redis(ip, consumer)
        return False
    conns.add(consumer)
    return True


def release_ip_connection(ip: str | None, consumer: object) -> None:
    """Remove a websocket connection from the active client registry."""

    if not ip:
        return
    _release_ip_connection_redis(ip, consumer)
    conns = ip_connections.get(ip)
    if not conns:
        return
    conns.discard(consumer)
    if not conns:
        ip_connections.pop(ip, None)


def pending_key(serial: str) -> str:
    """Return the key used before a connector id has been negotiated."""

    return f"{serial}{IDENTITY_SEPARATOR}{PENDING_SLUG}"


def _pending_metadata_key(message_id: str) -> str:
    return f"ocpp:pending:{message_id}"


def _pending_result_key(message_id: str) -> str:
    return f"ocpp:pending-result:{message_id}"


def _store_pending_metadata_redis(message_id: str, metadata: dict[str, object]) -> None:
    client = _state_redis()
    if not client:
        return
    try:
        client.set(_pending_metadata_key(message_id), json.dumps(metadata), ex=_PENDING_TTL)
    except RedisError:
        return


def _load_pending_metadata_redis(message_id: str) -> dict[str, object] | None:
    client = _state_redis()
    if not client:
        return None
    try:
        raw = client.get(_pending_metadata_key(message_id))
        return json.loads(raw) if raw else None
    except (RedisError, json.JSONDecodeError):
        return None


def _store_pending_result_redis(message_id: str, payload: dict[str, object]) -> None:
    client = _state_redis()
    if not client:
        return
    try:
        client.set(_pending_result_key(message_id), json.dumps(payload), ex=_PENDING_TTL)
    except RedisError:
        return


def _load_pending_result_redis(message_id: str) -> dict[str, object] | None:
    client = _state_redis()
    if not client:
        return None
    try:
        raw = client.get(_pending_result_key(message_id))
        return json.loads(raw) if raw else None
    except (RedisError, json.JSONDecodeError):
        return None


def _clear_pending_redis(message_id: str) -> None:
    client = _state_redis()
    if not client:
        return
    try:
        client.delete(_pending_metadata_key(message_id))
        client.delete(_pending_result_key(message_id))
    except RedisError:
        return


def _candidate_keys(serial: str, connector: int | str | None) -> list[str]:
    """Return possible keys for lookups with fallbacks."""

    keys: list[str] = []
    if connector not in (None, "", AGGREGATE_SLUG):
        keys.append(identity_key(serial, connector))
    else:
        keys.append(identity_key(serial, None))
        prefix = f"{serial}{IDENTITY_SEPARATOR}"
        for key in connections.keys():
            if key.startswith(prefix) and key not in keys:
                keys.append(key)
    keys.append(pending_key(serial))
    keys.append(serial)
    seen: set[str] = set()
    result: list[str] = []
    for key in keys:
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return result


def iter_identity_keys(serial: str) -> list[str]:
    """Return all known keys for the provided serial."""

    prefix = f"{serial}{IDENTITY_SEPARATOR}"
    keys = [key for key in connections.keys() if key.startswith(prefix)]
    if serial in connections:
        keys.append(serial)
    return keys


def is_connected(serial: str, connector: int | str | None = None) -> bool:
    """Return whether a connection exists for the provided charger identity."""

    if connector in (None, "", AGGREGATE_SLUG):
        prefix = f"{serial}{IDENTITY_SEPARATOR}"
        return (
            any(key.startswith(prefix) for key in connections) or serial in connections
        )
    return any(key in connections for key in _candidate_keys(serial, connector))


def get_connection(serial: str, connector: int | str | None = None):
    """Return the websocket consumer for the requested identity, if any."""

    for key in _candidate_keys(serial, connector):
        conn = connections.get(key)
        if conn is not None:
            return conn
    return None


def set_connection(serial: str, connector: int | str | None, consumer) -> str:
    """Store a websocket consumer under the negotiated identity."""

    key = identity_key(serial, connector)
    connections[key] = consumer
    return key


def pop_connection(serial: str, connector: int | str | None = None):
    """Remove a stored connection for the given identity."""

    for key in _candidate_keys(serial, connector):
        conn = connections.pop(key, None)
        if conn is not None:
            return conn
    return None


def get_transaction(serial: str, connector: int | str | None = None):
    """Return the active transaction for the provided identity."""

    for key in _candidate_keys(serial, connector):
        tx = transactions.get(key)
        if tx is not None:
            return tx
    return None


def set_transaction(serial: str, connector: int | str | None, tx) -> str:
    """Store an active transaction under the provided identity."""

    key = identity_key(serial, connector)
    transactions[key] = tx
    return key


def pop_transaction(serial: str, connector: int | str | None = None):
    """Remove and return an active transaction for the identity."""

    for key in _candidate_keys(serial, connector):
        tx = transactions.pop(key, None)
        if tx is not None:
            return tx
    return None


def register_pending_call(message_id: str, metadata: dict[str, object]) -> None:
    """Store metadata about an outstanding CSMS call."""

    copy = dict(metadata)
    with _pending_call_lock:
        pending_calls[message_id] = copy
        event = threading.Event()
        _pending_call_events[message_id] = event
        _pending_call_results.pop(message_id, None)
        handle = _pending_call_handles.pop(message_id, None)
    if handle:
        _cancel_timer_handle(handle)
    _store_pending_metadata_redis(message_id, copy)


def register_monitoring_report_request(request_id: int, metadata: dict[str, object]) -> None:
    """Track a monitoring report request by request id."""

    if request_id is None:
        return
    copy = dict(metadata)
    with _monitoring_report_lock:
        monitoring_report_requests[request_id] = copy


def get_monitoring_report_request(request_id: int) -> dict[str, object] | None:
    """Return metadata for a pending monitoring report request."""

    with _monitoring_report_lock:
        return monitoring_report_requests.get(request_id)


def pop_monitoring_report_request(request_id: int) -> dict[str, object] | None:
    """Remove and return metadata for a pending monitoring report request."""

    with _monitoring_report_lock:
        return monitoring_report_requests.pop(request_id, None)


def pop_pending_call(message_id: str) -> dict[str, object] | None:
    """Return and remove metadata for a previously registered call."""

    with _pending_call_lock:
        metadata = pending_calls.pop(message_id, None)
        handle = _pending_call_handles.pop(message_id, None)
    if handle:
        _cancel_timer_handle(handle)
    if metadata is None:
        metadata = _load_pending_metadata_redis(message_id)
    _clear_pending_redis(message_id)
    return metadata


def record_pending_call_result(
    message_id: str,
    *,
    metadata: dict[str, object] | None = None,
    success: bool = True,
    payload: object | None = None,
    error_code: str | None = None,
    error_description: str | None = None,
    error_details: object | None = None,
) -> None:
    """Record the outcome for a previously registered pending call."""

    result = {
        "metadata": dict(metadata or {}),
        "success": success,
        "payload": payload,
        "error_code": error_code,
        "error_description": error_description,
        "error_details": error_details,
    }
    with _pending_call_lock:
        _pending_call_results[message_id] = result
        event = _pending_call_events.pop(message_id, None)
        handle = _pending_call_handles.pop(message_id, None)
    if handle:
        _cancel_timer_handle(handle)
    if event:
        event.set()
    _store_pending_result_redis(message_id, result)


def wait_for_pending_call(
    message_id: str, *, timeout: float = 5.0
) -> dict[str, object] | None:
    """Wait for a pending call to be resolved and return the stored result."""

    with _pending_call_lock:
        existing = _pending_call_results.pop(message_id, None)
        if existing is not None:
            return existing
        event = _pending_call_events.get(message_id)
    if not event:
        cached = _load_pending_result_redis(message_id)
        if cached is not None:
            _clear_pending_redis(message_id)
            return cached
    if not event:
        return None
    if not event.wait(timeout):
        cached = _load_pending_result_redis(message_id)
        if cached is not None:
            _clear_pending_redis(message_id)
            return cached
        return None
    with _pending_call_lock:
        result = _pending_call_results.pop(message_id, None)
        _pending_call_events.pop(message_id, None)
        return result


def schedule_call_timeout(
    message_id: str,
    *,
    timeout: float = 5.0,
    action: str | None = None,
    log_key: str | None = None,
    log_type: str = "charger",
    message: str | None = None,
) -> None:
    """Schedule a timeout notice if a pending call is not answered."""

    loop = _ensure_scheduler_loop()

    def _notify() -> None:
        target_log: str | None = None
        entry_label: str | None = None
        with _pending_call_lock:
            _pending_call_handles.pop(message_id, None)
            metadata = pending_calls.get(message_id)
            if not metadata:
                return
            if action and metadata.get("action") != action:
                return
            if metadata.get("timeout_notice_sent"):
                return
            target_log = log_key or metadata.get("log_key")
            if not target_log:
                metadata["timeout_notice_sent"] = True
                return
            entry_label = message
            if not entry_label:
                action_label = action or str(metadata.get("action") or "Call")
                entry_label = f"{action_label} request timed out"
            metadata["timeout_notice_sent"] = True
        if target_log and entry_label:
            add_log(target_log, entry_label, log_type=log_type)

    future: concurrent.futures.Future[asyncio.TimerHandle] = concurrent.futures.Future()

    def _schedule_timer() -> None:
        try:
            handle = loop.call_later(timeout, _notify)
        except Exception as exc:  # pragma: no cover - defensive
            future.set_exception(exc)
            return
        future.set_result(handle)

    loop.call_soon_threadsafe(_schedule_timer)
    handle = future.result()

    with _pending_call_lock:
        previous = _pending_call_handles.pop(message_id, None)
        _pending_call_handles[message_id] = handle
    if previous:
        _cancel_timer_handle(previous)


def register_triggered_followup(
    serial: str,
    action: str,
    *,
    connector: int | str | None = None,
    log_key: str | None = None,
    target: str | None = None,
) -> None:
    """Record that ``serial`` should send ``action`` after a TriggerMessage."""

    entry = {
        "action": action,
        "connector": connector_slug(connector),
        "log_key": log_key,
        "target": target,
    }
    triggered_followups.setdefault(serial, []).append(entry)


def consume_triggered_followup(
    serial: str, action: str, connector: int | str | None = None
) -> dict[str, object] | None:
    """Return metadata for a previously registered follow-up message."""

    entries = triggered_followups.get(serial)
    if not entries:
        return None
    connector_slug_value = connector_slug(connector)
    for index, entry in enumerate(entries):
        if entry.get("action") != action:
            continue
        expected_slug = entry.get("connector")
        if expected_slug == AGGREGATE_SLUG:
            matched = True
        else:
            matched = connector_slug_value == expected_slug
        if not matched:
            continue
        result = entries.pop(index)
        if not entries:
            triggered_followups.pop(serial, None)
        return result
    return None


def clear_pending_calls(serial: str) -> None:
    """Remove any pending calls associated with the provided charger id."""

    to_cancel: list[asyncio.TimerHandle] = []
    with _pending_call_lock:
        to_remove = [
            key
            for key, value in pending_calls.items()
            if value.get("charger_id") == serial
        ]
        for key in to_remove:
            pending_calls.pop(key, None)
            _pending_call_events.pop(key, None)
            _pending_call_results.pop(key, None)
            handle = _pending_call_handles.pop(key, None)
            if handle:
                to_cancel.append(handle)
            _clear_pending_redis(key)
    for handle in to_cancel:
        _cancel_timer_handle(handle)
    with _monitoring_report_lock:
        stale_request_ids = [
            request_id
            for request_id, metadata in monitoring_report_requests.items()
            if metadata.get("charger_id") == serial
        ]
        for request_id in stale_request_ids:
            monitoring_report_requests.pop(request_id, None)
    with _transaction_requests_lock:
        stale_request_ids = [
            request_id
            for request_id, metadata in transaction_requests.items()
            if metadata.get("charger_id") == serial
        ]
        for request_id in stale_request_ids:
            metadata = transaction_requests.pop(request_id, None)
            if not metadata:
                continue
            connector_key = _transaction_connector_key(
                str(metadata.get("charger_id") or ""), metadata.get("connector_id")
            )
            transaction_key = _normalize_transaction_id(
                metadata.get("transaction_id") or metadata.get("ocpp_transaction_id")
            )
            _remove_transaction_index_entry(
                _transaction_requests_by_connector, connector_key, request_id
            )
            _remove_transaction_index_entry(
                _transaction_requests_by_transaction, transaction_key, request_id
            )
    charging_profile_reports.pop(serial, None)


def restore_pending_calls(serial: str) -> list[str]:
    """Reload any pending calls for ``serial`` that were persisted to Redis."""

    client = _state_redis()
    restored: list[str] = []
    if not client:
        return restored
    try:
        for key in client.scan_iter(_pending_metadata_key("*")):
            raw = client.get(key)
            if not raw:
                continue
            try:
                metadata = json.loads(raw)
            except json.JSONDecodeError:
                continue
            charger_id = str(metadata.get("charger_id") or "").lower()
            if not charger_id or charger_id != serial.lower():
                continue
            message_id = key.rsplit(":", 1)[-1]
            with _pending_call_lock:
                if message_id in pending_calls:
                    continue
            register_pending_call(message_id, metadata)
            restored.append(message_id)
    except RedisError:
        return restored
    return restored


def _run_scheduler_loop(
    loop: asyncio.AbstractEventLoop, ready: threading.Event
) -> None:
    asyncio.set_event_loop(loop)
    ready.set()
    loop.run_forever()


def _ensure_scheduler_loop() -> asyncio.AbstractEventLoop:
    global _scheduler_loop, _scheduler_thread

    loop = _scheduler_loop
    if loop and loop.is_running():
        return loop
    with _scheduler_lock:
        loop = _scheduler_loop
        if loop and loop.is_running():
            return loop
        loop = asyncio.new_event_loop()
        ready = threading.Event()
        thread = threading.Thread(
            target=_run_scheduler_loop,
            args=(loop, ready),
            name="ocpp-store-scheduler",
            daemon=True,
        )
        thread.start()
        ready.wait()
        _scheduler_loop = loop
        _scheduler_thread = thread
        return loop


def _cancel_timer_handle(handle: asyncio.TimerHandle) -> None:
    loop = _scheduler_loop
    if loop and loop.is_running():
        loop.call_soon_threadsafe(handle.cancel)
    else:  # pragma: no cover - loop stopped during shutdown
        handle.cancel()


def reassign_identity(old_key: str, new_key: str) -> str:
    """Move any stored data from ``old_key`` to ``new_key``."""

    if old_key == new_key:
        return new_key
    if not old_key:
        return new_key
    for mapping in (connections, transactions, history):
        if old_key in mapping:
            mapping[new_key] = mapping.pop(old_key)
    for log_type in logs:
        store = logs[log_type]
        if old_key in store:
            store[new_key] = store.pop(old_key)
    for log_type in log_names:
        names = log_names[log_type]
        if old_key in names:
            names[new_key] = names.pop(old_key)
    return new_key


async def _touch_lock() -> None:
    try:
        while True:
            SESSION_LOCK.touch()
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        pass


def start_session_lock() -> None:
    global _lock_task
    SESSION_LOCK.touch()
    loop = asyncio.get_event_loop()
    if _lock_task is None or _lock_task.done():
        _lock_task = loop.create_task(_touch_lock())


def stop_session_lock() -> None:
    global _lock_task
    if _lock_task:
        _lock_task.cancel()
        _lock_task = None
    if SESSION_LOCK.exists():
        SESSION_LOCK.unlink()


def register_log_name(cid: str, name: str, log_type: str = "charger") -> None:
    """Register a friendly name for the id used in log files."""

    names = log_names[log_type]
    # Ensure lookups are case-insensitive by overwriting any existing entry
    # that matches the provided cid regardless of case.
    for key in list(names.keys()):
        if key.lower() == cid.lower():
            cid = key
            break
    names[cid] = name


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w.-]", "_", name)


def _file_path(cid: str, log_type: str = "charger") -> Path:
    name = log_names[log_type].get(cid, cid)
    return LOG_DIR / f"{log_type}.{_safe_name(name)}.log"


def add_log(cid: str, entry: str, log_type: str = "charger") -> None:
    """Append a timestamped log entry for the given id and log type."""

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    entry = f"{timestamp} {entry}"

    key = _append_memory_log(cid, entry, log_type=log_type)
    _write_log_file(key, entry, log_type=log_type)


def _append_memory_log(cid: str, entry: str, *, log_type: str) -> str:
    store = logs[log_type]
    # Store log entries under the cid as provided but allow retrieval using
    # any casing by recording entries in a case-insensitive manner.
    buffer = None
    lower = cid.lower()
    key = cid
    for existing_key, entries in store.items():
        if existing_key.lower() == lower:
            key = existing_key
            buffer = entries
            break
    if buffer is None:
        buffer = deque(maxlen=MAX_IN_MEMORY_LOG_ENTRIES)
        store[key] = buffer
    elif buffer.maxlen != MAX_IN_MEMORY_LOG_ENTRIES:
        buffer = deque(buffer, maxlen=MAX_IN_MEMORY_LOG_ENTRIES)
        store[key] = buffer
    buffer.append(entry)
    return key


def _write_log_file(cid: str, entry: str, *, log_type: str) -> None:
    path = _file_path(cid, log_type)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(entry + "\n")


def start_log_capture(
    serial: str, connector: int | str | None, request_id: int, *, name: str | None = None
) -> str:
    """Begin recording a GetLog capture using the session log pipeline."""

    base_key = identity_key(serial, connector)
    capture_key = f"{base_key}-log-{request_id}"
    base_name = log_names["charger"].get(base_key, base_key)
    label = name or f"{base_name}-log-{request_id}"
    register_log_name(capture_key, label, log_type="charger")
    start_session_log(capture_key, request_id)
    return capture_key


def append_log_capture(capture_key: str, message: str) -> None:
    """Append a message to an active GetLog capture."""

    add_session_message(capture_key, message)


def finalize_log_capture(capture_key: str) -> None:
    """Finalize a GetLog capture created via :func:`start_log_capture`."""

    end_session_log(capture_key)


def _session_folder(cid: str) -> Path:
    """Return the folder path for session logs for the given charger."""

    name = log_names["charger"].get(cid, cid)
    folder = SESSION_DIR / _safe_name(name)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def start_session_log(cid: str, tx_id: int) -> None:
    """Begin logging a session for the given charger and transaction id."""

    existing = history.pop(cid, None)
    if existing:
        try:
            _finalize_session(existing)
        except Exception:
            # If finalizing the previous session fails we still want to reset
            # the session metadata so the new session can proceed.
            pass

    start = datetime.now(timezone.utc)
    folder = _session_folder(cid)
    date = start.strftime("%Y%m%d")
    filename = f"{date}_{tx_id}.json"
    path = folder / filename
    history[cid] = {
        "transaction": tx_id,
        "start": start,
        "path": path,
        "buffer": [],
        "first": True,
    }
    with path.open("w", encoding="utf-8") as handle:
        handle.write("[")


def add_session_message(cid: str, message: str) -> None:
    """Record a raw message for the current session if one is active."""

    sess = history.get(cid)
    if not sess:
        return
    buffer: list[str] = sess.setdefault("buffer", [])
    payload = json.dumps(
        {
            "timestamp": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "message": message,
        },
        ensure_ascii=False,
    )
    buffer.append(payload)
    if len(buffer) >= SESSION_LOG_BUFFER_LIMIT:
        _flush_session_buffer(sess)


def end_session_log(cid: str) -> None:
    """Write any recorded session log to disk for the given charger."""

    sess = history.pop(cid, None)
    if not sess:
        return
    _finalize_session(sess)


def _flush_session_buffer(sess: dict[str, object]) -> None:
    path: Path | None = sess.get("path") if isinstance(sess.get("path"), Path) else None
    buffer = sess.get("buffer")
    if path is None or not buffer:
        return
    first = bool(sess.get("first", True))
    with path.open("a", encoding="utf-8") as handle:
        for raw in list(buffer):
            if not first:
                handle.write(",")
            handle.write("\n  ")
            handle.write(raw)
            first = False
        handle.flush()
    buffer.clear()
    sess["first"] = first


def _finalize_session(sess: dict[str, object]) -> None:
    try:
        _flush_session_buffer(sess)
        path: Path | None = sess.get("path") if isinstance(sess.get("path"), Path) else None
        if path:
            with path.open("a", encoding="utf-8") as handle:
                if sess.get("first", True):
                    handle.write("]\n")
                else:
                    handle.write("\n]\n")
                handle.flush()
    finally:
        sess["first"] = True


def _log_key_candidates(cid: str, log_type: str) -> list[str]:
    """Return log identifiers to inspect for the requested cid."""

    if IDENTITY_SEPARATOR not in cid:
        return [cid]
    serial, slug = cid.split(IDENTITY_SEPARATOR, 1)
    slug = slug or AGGREGATE_SLUG
    if slug != AGGREGATE_SLUG:
        return [cid]
    keys: list[str] = [identity_key(serial, None)]
    prefix = f"{serial}{IDENTITY_SEPARATOR}"
    for source in (log_names[log_type], logs[log_type]):
        for key in source.keys():
            if key.startswith(prefix) and key not in keys:
                keys.append(key)
    return keys


def _resolve_log_identifier(cid: str, log_type: str) -> tuple[str, str | None]:
    """Return the canonical key and friendly name for ``cid``."""

    names = log_names[log_type]
    name = names.get(cid)
    if name is None:
        lower = cid.lower()
        for key, value in names.items():
            if key.lower() == lower:
                cid = key
                name = value
                break
        else:
            try:
                if log_type == "simulator":
                    from .models import Simulator

                    sim = Simulator.objects.filter(cp_path__iexact=cid).first()
                    if sim:
                        cid = sim.cp_path
                        name = sim.name
                        names[cid] = name
                else:
                    from .models import Charger

                    serial = cid.split(IDENTITY_SEPARATOR, 1)[0]
                    ch = Charger.objects.filter(charger_id__iexact=serial).first()
                    if ch and ch.name:
                        name = ch.name
                        names[cid] = name
            except Exception:  # pragma: no cover - best effort lookup
                pass
    return cid, name


def _log_file_for_identifier(cid: str, name: str | None, log_type: str) -> Path:
    path = _file_path(cid, log_type)
    if not path.exists():
        candidates = [_safe_name(name or cid).lower()]
        cid_candidate = _safe_name(cid).lower()
        if cid_candidate not in candidates:
            candidates.append(cid_candidate)
        for candidate in candidates:
            target = f"{log_type}.{candidate}"
            for file in LOG_DIR.glob(f"{log_type}.*.log"):
                if file.stem.lower() == target:
                    path = file
                    break
            if path.exists():
                break
    return path


def _memory_logs_for_identifier(cid: str, log_type: str) -> list[str]:
    store = logs[log_type]
    lower = cid.lower()
    for key, entries in store.items():
        if key.lower() == lower:
            return list(entries)
    return []


def _parse_log_timestamp(entry: str) -> datetime | None:
    """Return the parsed timestamp for a log entry, if available."""

    if len(entry) < 24:
        return None
    try:
        timestamp = datetime.strptime(entry[:23], "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        return None
    return timestamp.replace(tzinfo=timezone.utc)


def _iter_file_lines_reverse(path: Path, *, limit: int | None = None) -> Iterator[str]:
    """Yield lines from ``path`` starting with the newest entries."""

    if not path.exists():
        return

    chunk_size = 4096
    remaining = limit
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        buffer = b""
        while position > 0:
            read_size = min(chunk_size, position)
            position -= read_size
            handle.seek(position)
            chunk = handle.read(read_size)
            buffer = chunk + buffer
            lines = buffer.split(b"\n")
            buffer = lines.pop(0)
            for line in reversed(lines):
                if not line:
                    continue
                try:
                    text = line.decode("utf-8")
                except UnicodeDecodeError:
                    text = line.decode("utf-8", errors="ignore")
                yield text
                if remaining is not None:
                    remaining -= 1
                    if remaining <= 0:
                        return
        if buffer:
            try:
                text = buffer.decode("utf-8")
            except UnicodeDecodeError:
                text = buffer.decode("utf-8", errors="ignore")
            if text:
                yield text


def _iter_log_entries_for_key(
    cid: str,
    name: str | None,
    log_type: str,
    *,
    since: datetime | None = None,
    limit: int | None = None,
) -> Iterator[LogEntry]:
    """Yield structured log entries for a specific identifier."""

    yielded = 0
    seen_for_key: set[str] = set()
    memory_entries = _memory_logs_for_identifier(cid, log_type)
    for entry in reversed(memory_entries):
        if entry in seen_for_key:
            continue
        timestamp = _parse_log_timestamp(entry)
        if timestamp is None:
            continue
        seen_for_key.add(entry)
        yield LogEntry(timestamp=timestamp, text=entry)
        yielded += 1
        if since is not None and timestamp < since:
            return
        if limit is not None and yielded >= limit:
            return

    path = _log_file_for_identifier(cid, name, log_type)
    file_limit = None
    if limit is not None:
        file_limit = max(limit - yielded, 0)
        if file_limit == 0:
            return
    for entry in _iter_file_lines_reverse(path, limit=file_limit):
        if entry in seen_for_key:
            continue
        timestamp = _parse_log_timestamp(entry)
        if timestamp is None:
            continue
        seen_for_key.add(entry)
        yield LogEntry(timestamp=timestamp, text=entry)
        yielded += 1
        if since is not None and timestamp < since:
            return
        if limit is not None and yielded >= limit:
            return


def iter_log_entries(
    identifiers: str | Iterable[str],
    log_type: str = "charger",
    *,
    since: datetime | None = None,
    limit: int | None = None,
) -> Iterator[LogEntry]:
    """Yield log entries ordered from newest to oldest.

    ``identifiers`` may be a single charger identifier or an iterable of
    identifiers. Results are de-duplicated across matching memory and file
    sources and iteration stops once entries fall before ``since`` or ``limit``
    is reached.
    """

    if isinstance(identifiers, str):
        requested: list[str] = [identifiers]
    else:
        requested = list(identifiers)

    seen_keys: set[str] = set()
    sources: list[tuple[str, str | None]] = []
    for identifier in requested:
        for key in _log_key_candidates(identifier, log_type):
            lower_key = key.lower()
            if lower_key in seen_keys:
                continue
            seen_keys.add(lower_key)
            resolved, name = _resolve_log_identifier(key, log_type)
            sources.append((resolved, name))

    heap: list[tuple[float, int, LogEntry, Iterator[LogEntry]]] = []
    counter = itertools.count()
    seen_entries: set[str] = set()
    total_yielded = 0

    for resolved, name in sources:
        iterator = _iter_log_entries_for_key(
            resolved,
            name,
            log_type,
            since=since,
            limit=limit,
        )
        try:
            entry = next(iterator)
        except StopIteration:
            continue
        heapq.heappush(
            heap,
            (
                -entry.timestamp.timestamp(),
                next(counter),
                entry,
                iterator,
            ),
        )

    while heap:
        _, _, entry, iterator = heapq.heappop(heap)
        if entry.text not in seen_entries:
            seen_entries.add(entry.text)
            yield entry
            total_yielded += 1
            if limit is not None and total_yielded >= limit:
                return
            if since is not None and entry.timestamp < since:
                return
        try:
            next_entry = next(iterator)
        except StopIteration:
            continue
        heapq.heappush(
            heap,
            (
                -next_entry.timestamp.timestamp(),
                next(counter),
                next_entry,
                iterator,
            ),
        )


def get_logs(
    cid: str, log_type: str = "charger", *, limit: int | None = None
) -> list[str]:
    """Return all log entries for the given id and type."""

    entries_list: list[str] = []
    max_entries: int | None = None
    entries_deque: deque[str] | None = None
    if limit is not None:
        try:
            parsed_limit = int(limit)
        except (TypeError, ValueError):
            parsed_limit = None
        if parsed_limit is not None and parsed_limit > 0:
            max_entries = parsed_limit
            entries_deque = deque(maxlen=max_entries)

    seen_paths: set[Path] = set()
    seen_keys: set[str] = set()
    for key in _log_key_candidates(cid, log_type):
        resolved, name = _resolve_log_identifier(key, log_type)
        path = _log_file_for_identifier(resolved, name, log_type)
        if path.exists() and path not in seen_paths:
            if max_entries is None:
                entries_list.extend(path.read_text(encoding="utf-8").splitlines())
            else:
                with path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        if entries_deque is not None:
                            entries_deque.append(line.rstrip("\r\n"))
            seen_paths.add(path)
        memory_entries = _memory_logs_for_identifier(resolved, log_type)
        lower_key = resolved.lower()
        if memory_entries and lower_key not in seen_keys:
            if max_entries is None:
                entries_list.extend(memory_entries)
            elif entries_deque is not None:
                for entry in memory_entries:
                    entries_deque.append(entry)
            seen_keys.add(lower_key)
    if max_entries is None:
        return entries_list
    if entries_deque is None:
        return []
    return list(entries_deque)


def clear_log(cid: str, log_type: str = "charger") -> None:
    """Remove any stored logs for the given id and type."""
    for key in _log_key_candidates(cid, log_type):
        store_map = logs[log_type]
        resolved = next(
            (k for k in list(store_map.keys()) if k.lower() == key.lower()),
            key,
        )
        store_map.pop(resolved, None)
        path = _file_path(resolved, log_type)
        if not path.exists():
            target = f"{log_type}.{_safe_name(log_names[log_type].get(resolved, resolved)).lower()}"
            for file in LOG_DIR.glob(f"{log_type}.*.log"):
                if file.stem.lower() == target:
                    path = file
                    break
        if path.exists():
            path.unlink()
