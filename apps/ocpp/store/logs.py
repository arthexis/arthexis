"""Log buffers and file-backed persistence for the OCPP store."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
import heapq
import itertools
import json
import os
from pathlib import Path
import re
from typing import Iterable, Iterator

from django.utils import timezone

from apps.loggers.paths import select_log_dir

from . import state

# Maximum number of recent log entries to keep in memory per identity.
MAX_IN_MEMORY_LOG_ENTRIES = 1000

logs: dict[str, dict[str, deque[str]]] = {"charger": {}, "simulator": {}}
# store per charger session logs before they are flushed to disk
history: dict[str, dict[str, object]] = {}

# mapping of charger id / cp_path to friendly names used for log files
log_names: dict[str, dict[str, str]] = {"charger": {}, "simulator": {}}

BASE_DIR = Path(__file__).resolve().parents[3]
LOG_DIR = select_log_dir(BASE_DIR)
SESSION_DIR = LOG_DIR / "sessions"
LOCK_DIR = BASE_DIR / ".locks"
SESSION_LOCK = LOCK_DIR / "charging.lck"
_lock_task: asyncio.Task | None = None

SESSION_LOG_BUFFER_LIMIT = 16


def ensure_log_dirs_exist() -> None:
    """Ensure log directories exist without failing on permission issues."""

    try:
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    try:
        LOCK_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


@dataclass(frozen=True)
class LogEntry:
    """Structured log entry returned by :func:`iter_log_entries`."""

    timestamp: datetime
    text: str


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w.-]", "_", name)


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


def _charger_log_basename(cid: str) -> str:
    serial = cid.split(state.IDENTITY_SEPARATOR, 1)[0]
    return _safe_name(serial)


def _file_path(cid: str, log_type: str = "charger") -> Path:
    if log_type == "charger":
        basename = _charger_log_basename(cid)
    else:
        name = log_names[log_type].get(cid, cid)
        basename = _safe_name(name)
    return LOG_DIR / f"{log_type}.{basename}.log"


def add_log(cid: str, entry: str, log_type: str = "charger") -> None:
    """Append a timestamped log entry for the given id and log type."""

    timestamp = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
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
    ensure_log_dirs_exist()
    path = _file_path(cid, log_type)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(entry + "\n")


def _session_folder(cid: str) -> Path:
    """Return the folder path for session logs for the given charger."""

    ensure_log_dirs_exist()
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

    start = datetime.now(dt_timezone.utc)
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
            "timestamp": datetime.now(dt_timezone.utc)
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


async def _touch_lock() -> None:
    try:
        while True:
            SESSION_LOCK.touch()
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        SESSION_LOCK.touch()
        raise


def start_session_lock() -> None:
    global _lock_task
    ensure_log_dirs_exist()
    SESSION_LOCK.touch()
    loop = asyncio.get_running_loop()
    if _lock_task is None or _lock_task.done():
        _lock_task = loop.create_task(_touch_lock())


def stop_session_lock() -> None:
    global _lock_task
    if _lock_task:
        _lock_task.cancel()
        _lock_task = None
    if SESSION_LOCK.exists():
        SESSION_LOCK.unlink()


def start_log_capture(
    serial: str, connector: int | str | None, request_id: int, *, name: str | None = None
) -> str:
    """Begin recording a GetLog capture using the session log pipeline."""

    base_key = state.identity_key(serial, connector)
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


def _log_key_candidates(cid: str, log_type: str) -> list[str]:
    """Return log identifiers to inspect for the requested cid."""

    if state.IDENTITY_SEPARATOR not in cid:
        return [cid]
    serial, slug = cid.split(state.IDENTITY_SEPARATOR, 1)
    slug = slug or state.AGGREGATE_SLUG
    if slug != state.AGGREGATE_SLUG:
        return [cid]
    keys: list[str] = [state.identity_key(serial, None)]
    prefix = f"{serial}{state.IDENTITY_SEPARATOR}"
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
                    from ..models import Simulator

                    sim = Simulator.objects.filter(cp_path__iexact=cid).first()
                    if sim:
                        cid = sim.cp_path
                        name = sim.name
                        names[cid] = name
                else:
                    from ..models import Charger

                    serial = cid.split(state.IDENTITY_SEPARATOR, 1)[0]
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
        if log_type == "charger":
            candidates = [
                _charger_log_basename(cid).lower(),
                _safe_name(name or cid).lower(),
            ]
        else:
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
    return timezone.make_aware(timestamp, timezone.get_current_timezone())


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


def iter_file_lines_reverse(path: Path, *, limit: int | None = None) -> Iterator[str]:
    """Yield lines from ``path`` starting with the newest entries."""

    return _iter_file_lines_reverse(path, limit=limit)


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


def get_logs(cid: str, log_type: str = "charger", *, limit: int | None = None) -> list[str]:
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


def resolve_log_path(identifier: str, *, log_type: str = "charger") -> Path | None:
    """Return the log path for an identifier if it exists."""

    resolved, name = _resolve_log_identifier(identifier, log_type)
    path = _log_file_for_identifier(resolved, name, log_type)
    return path if path.exists() else None


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


__all__ = [
    "BASE_DIR",
    "LOCK_DIR",
    "LOG_DIR",
    "LogEntry",
    "MAX_IN_MEMORY_LOG_ENTRIES",
    "SESSION_DIR",
    "SESSION_LOCK",
    "SESSION_LOG_BUFFER_LIMIT",
    "add_log",
    "add_session_message",
    "append_log_capture",
    "clear_log",
    "end_session_log",
    "finalize_log_capture",
    "get_logs",
    "history",
    "iter_file_lines_reverse",
    "iter_log_entries",
    "log_names",
    "logs",
    "register_log_name",
    "resolve_log_path",
    "start_log_capture",
    "start_session_lock",
    "start_session_log",
    "stop_session_lock",
]
