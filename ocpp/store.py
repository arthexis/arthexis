"""In-memory store for OCPP data with file backed logs."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import threading

from core.log_paths import select_log_dir

IDENTITY_SEPARATOR = "#"
AGGREGATE_SLUG = "all"
PENDING_SLUG = "pending"

MAX_CONNECTIONS_PER_IP = 2

connections: dict[str, object] = {}
transactions: dict[str, object] = {}
logs: dict[str, dict[str, list[str]]] = {"charger": {}, "simulator": {}}
# store per charger session logs before they are flushed to disk
history: dict[str, dict[str, object]] = {}
simulators = {}
ip_connections: dict[str, set[object]] = {}
pending_calls: dict[str, dict[str, object]] = {}
_pending_call_events: dict[str, threading.Event] = {}
_pending_call_results: dict[str, dict[str, object]] = {}
_pending_call_lock = threading.Lock()
triggered_followups: dict[str, list[dict[str, object]]] = {}

# mapping of charger id / cp_path to friendly names used for log files
log_names: dict[str, dict[str, str]] = {"charger": {}, "simulator": {}}

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = select_log_dir(BASE_DIR)
SESSION_DIR = LOG_DIR / "sessions"
SESSION_DIR.mkdir(exist_ok=True)
LOCK_DIR = BASE_DIR / "locks"
LOCK_DIR.mkdir(exist_ok=True)
SESSION_LOCK = LOCK_DIR / "charging.lck"
_lock_task: asyncio.Task | None = None


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


def register_ip_connection(ip: str | None, consumer: object) -> bool:
    """Track a websocket connection for the provided client IP."""

    if not ip:
        return True
    conns = ip_connections.setdefault(ip, set())
    if consumer in conns:
        return True
    if len(conns) >= MAX_CONNECTIONS_PER_IP:
        return False
    conns.add(consumer)
    return True


def release_ip_connection(ip: str | None, consumer: object) -> None:
    """Remove a websocket connection from the active client registry."""

    if not ip:
        return
    conns = ip_connections.get(ip)
    if not conns:
        return
    conns.discard(consumer)
    if not conns:
        ip_connections.pop(ip, None)


def pending_key(serial: str) -> str:
    """Return the key used before a connector id has been negotiated."""

    return f"{serial}{IDENTITY_SEPARATOR}{PENDING_SLUG}"


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


def pop_pending_call(message_id: str) -> dict[str, object] | None:
    """Return and remove metadata for a previously registered call."""

    with _pending_call_lock:
        return pending_calls.pop(message_id, None)


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
    if event:
        event.set()


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
        return None
    if not event.wait(timeout):
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

    def _notify() -> None:
        with _pending_call_lock:
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
        label = message
        if not label:
            action_label = action or str(metadata.get("action") or "Call")
            label = f"{action_label} request timed out"
        add_log(target_log, label, log_type=log_type)
        metadata["timeout_notice_sent"] = True

    timer = threading.Timer(timeout, _notify)
    timer.daemon = True
    timer.start()


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
    triggered_followups.pop(serial, None)


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

    store = logs[log_type]
    # Store log entries under the cid as provided but allow retrieval using
    # any casing by recording entries in a case-insensitive manner.
    key = next((k for k in store.keys() if k.lower() == cid.lower()), cid)
    store.setdefault(key, []).append(entry)
    path = _file_path(key, log_type)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(entry + "\n")


def _session_folder(cid: str) -> Path:
    """Return the folder path for session logs for the given charger."""

    name = log_names["charger"].get(cid, cid)
    folder = SESSION_DIR / _safe_name(name)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def start_session_log(cid: str, tx_id: int) -> None:
    """Begin logging a session for the given charger and transaction id."""

    history[cid] = {
        "transaction": tx_id,
        "start": datetime.now(timezone.utc),
        "messages": [],
    }


def add_session_message(cid: str, message: str) -> None:
    """Record a raw message for the current session if one is active."""

    sess = history.get(cid)
    if not sess:
        return
    sess["messages"].append(
        {
            "timestamp": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "message": message,
        }
    )


def end_session_log(cid: str) -> None:
    """Write any recorded session log to disk for the given charger."""

    sess = history.pop(cid, None)
    if not sess:
        return
    folder = _session_folder(cid)
    date = sess["start"].strftime("%Y%m%d")
    tx_id = sess.get("transaction")
    filename = f"{date}_{tx_id}.json"
    path = folder / filename
    with path.open("w", encoding="utf-8") as handle:
        json.dump(sess["messages"], handle, ensure_ascii=False, indent=2)


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
        target = f"{log_type}.{_safe_name(name or cid).lower()}"
        for file in LOG_DIR.glob(f"{log_type}.*.log"):
            if file.stem.lower() == target:
                path = file
                break
    return path


def _memory_logs_for_identifier(cid: str, log_type: str) -> list[str]:
    store = logs[log_type]
    lower = cid.lower()
    for key, entries in store.items():
        if key.lower() == lower:
            return entries
    return []


def get_logs(cid: str, log_type: str = "charger") -> list[str]:
    """Return all log entries for the given id and type."""

    entries: list[str] = []
    seen_paths: set[Path] = set()
    seen_keys: set[str] = set()
    for key in _log_key_candidates(cid, log_type):
        resolved, name = _resolve_log_identifier(key, log_type)
        path = _log_file_for_identifier(resolved, name, log_type)
        if path.exists() and path not in seen_paths:
            entries.extend(path.read_text(encoding="utf-8").splitlines())
            seen_paths.add(path)
        memory_entries = _memory_logs_for_identifier(resolved, log_type)
        lower_key = resolved.lower()
        if memory_entries and lower_key not in seen_keys:
            entries.extend(memory_entries)
            seen_keys.add(lower_key)
    return entries


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
