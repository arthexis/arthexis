"""Pending call tracking, events, and TTL management for the OCPP store."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import threading

from django.conf import settings
from redis.exceptions import RedisError

from . import logs, scheduler, state
from .transactions import (
    _normalize_transaction_id,
    _remove_transaction_index_entry,
    _transaction_connector_key,
    _transaction_requests_by_connector,
    _transaction_requests_by_transaction,
    _transaction_requests_lock,
    transaction_requests,
)

_PENDING_TTL = int(getattr(settings, "OCPP_PENDING_CALL_TTL", 1800) or 1800)

pending_calls: dict[str, dict[str, object]] = {}
_pending_call_events: dict[str, threading.Event] = {}
_pending_call_results: dict[str, dict[str, object]] = {}
_pending_call_lock = threading.Lock()
_pending_call_handles: dict[str, asyncio.TimerHandle] = {}
triggered_followups: dict[str, list[dict[str, object]]] = {}
monitoring_report_requests: dict[int, dict[str, object]] = {}
_monitoring_report_lock = threading.Lock()


def _pending_metadata_key(message_id: str) -> str:
    return f"ocpp:pending:{message_id}"


def _pending_result_key(message_id: str) -> str:
    return f"ocpp:pending-result:{message_id}"


def _store_pending_metadata_redis(message_id: str, metadata: dict[str, object]) -> None:
    client = state._state_redis()
    if not client:
        return
    try:
        client.set(_pending_metadata_key(message_id), json.dumps(metadata), ex=_PENDING_TTL)
    except RedisError:
        return


def _load_pending_metadata_redis(message_id: str) -> dict[str, object] | None:
    client = state._state_redis()
    if not client:
        return None
    try:
        raw = client.get(_pending_metadata_key(message_id))
        return json.loads(raw) if raw else None
    except (RedisError, json.JSONDecodeError):
        return None


def _store_pending_result_redis(message_id: str, payload: dict[str, object]) -> None:
    client = state._state_redis()
    if not client:
        return
    try:
        client.set(_pending_result_key(message_id), json.dumps(payload), ex=_PENDING_TTL)
    except RedisError:
        return


def _load_pending_result_redis(message_id: str) -> dict[str, object] | None:
    client = state._state_redis()
    if not client:
        return None
    try:
        raw = client.get(_pending_result_key(message_id))
        return json.loads(raw) if raw else None
    except (RedisError, json.JSONDecodeError):
        return None


def _clear_pending_redis(message_id: str) -> None:
    client = state._state_redis()
    if not client:
        return
    try:
        client.delete(_pending_metadata_key(message_id))
        client.delete(_pending_result_key(message_id))
    except RedisError:
        return


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
        scheduler._cancel_timer_handle(handle)
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
        scheduler._cancel_timer_handle(handle)
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
        scheduler._cancel_timer_handle(handle)
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

    loop = scheduler._ensure_scheduler_loop()

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
            logs.add_log(target_log, entry_label, log_type=log_type)

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
        scheduler._cancel_timer_handle(previous)


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
        "connector": state.connector_slug(connector),
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
    connector_slug_value = state.connector_slug(connector)
    for index, entry in enumerate(entries):
        if entry.get("action") != action:
            continue
        expected_slug = entry.get("connector")
        if expected_slug == state.AGGREGATE_SLUG:
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
        scheduler._cancel_timer_handle(handle)
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
    state.charging_profile_reports.pop(serial, None)


def restore_pending_calls(serial: str) -> list[str]:
    """Reload any pending calls for ``serial`` that were persisted to Redis."""

    client = state._state_redis()
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


__all__ = [
    "_PENDING_TTL",
    "_clear_pending_redis",
    "_load_pending_metadata_redis",
    "_load_pending_result_redis",
    "_monitoring_report_lock",
    "_pending_call_events",
    "_pending_call_handles",
    "_pending_call_lock",
    "_pending_call_results",
    "_pending_metadata_key",
    "_pending_result_key",
    "_store_pending_metadata_redis",
    "_store_pending_result_redis",
    "clear_pending_calls",
    "consume_triggered_followup",
    "get_monitoring_report_request",
    "monitoring_report_requests",
    "pending_calls",
    "pop_monitoring_report_request",
    "pop_pending_call",
    "record_pending_call_result",
    "register_monitoring_report_request",
    "register_pending_call",
    "register_triggered_followup",
    "restore_pending_calls",
    "schedule_call_timeout",
    "triggered_followups",
    "wait_for_pending_call",
]
