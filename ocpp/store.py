"""In-memory store for OCPP data with file backed logs."""

from __future__ import annotations

from pathlib import Path
from datetime import datetime
import json
import re
import asyncio

from core.log_paths import select_log_dir

connections = {}
transactions = {}
logs: dict[str, dict[str, list[str]]] = {"charger": {}, "simulator": {}}
# store per charger session logs before they are flushed to disk
history: dict[str, dict[str, object]] = {}
simulators = {}

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

    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
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
        "start": datetime.utcnow(),
        "messages": [],
    }


def add_session_message(cid: str, message: str) -> None:
    """Record a raw message for the current session if one is active."""

    sess = history.get(cid)
    if not sess:
        return
    sess["messages"].append(
        {
            "timestamp": datetime.utcnow().isoformat() + "Z",
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


def get_logs(cid: str, log_type: str = "charger") -> list[str]:
    """Return all log entries for the given id and type."""

    names = log_names[log_type]
    # Try to find a matching log name case-insensitively
    name = names.get(cid)
    if name is None:
        for key, value in names.items():
            if key.lower() == cid.lower():
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

                    ch = Charger.objects.filter(charger_id__iexact=cid).first()
                    if ch and ch.name:
                        cid = ch.charger_id
                        name = ch.name
                        names[cid] = name
            except Exception:  # pragma: no cover - best effort lookup
                pass

    path = _file_path(cid, log_type)
    if not path.exists():
        target = f"{log_type}.{_safe_name(name or cid).lower()}"
        for file in LOG_DIR.glob(f"{log_type}.*.log"):
            if file.stem.lower() == target:
                path = file
                break

    if path.exists():
        return path.read_text(encoding="utf-8").splitlines()

    store = logs[log_type]
    for key, entries in store.items():
        if key.lower() == cid.lower():
            return entries
    return []


def clear_log(cid: str, log_type: str = "charger") -> None:
    """Remove any stored logs for the given id and type."""

    store = logs[log_type]
    key = next((k for k in list(store.keys()) if k.lower() == cid.lower()), cid)
    store.pop(key, None)
    path = _file_path(key, log_type)
    if not path.exists():
        target = f"{log_type}.{_safe_name(log_names[log_type].get(key, key)).lower()}"
        for file in LOG_DIR.glob(f"{log_type}.*.log"):
            if file.stem.lower() == target:
                path = file
                break
    if path.exists():
        path.unlink()
