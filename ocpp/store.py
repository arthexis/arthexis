"""In-memory store for OCPP data with file backed logs."""

from __future__ import annotations

from pathlib import Path
import re

connections = {}
transactions = {}
logs: dict[str, dict[str, list[str]]] = {"charger": {}, "simulator": {}}
history = {}
simulators = {}

# mapping of charger id / cp_path to friendly names used for log files
log_names: dict[str, dict[str, str]] = {"charger": {}, "simulator": {}}

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)


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
    """Append a log entry for the given id and log type."""

    store = logs[log_type]
    # Store log entries under the cid as provided but allow retrieval using
    # any casing by recording entries in a case-insensitive manner.
    key = next((k for k in store.keys() if k.lower() == cid.lower()), cid)
    store.setdefault(key, []).append(entry)
    path = _file_path(key, log_type)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(entry + "\n")


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
