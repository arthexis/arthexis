"""In-memory store for OCPP data with file backed logs."""

from __future__ import annotations

from pathlib import Path
import re

connections = {}
transactions = {}
logs = {}
history = {}
simulators = {}

# mapping of charger id / cp_path to simulator name used for log files
log_names: dict[str, str] = {}

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)


def register_log_name(cid: str, name: str) -> None:
    """Register a friendly name for the charger id used in log files."""

    # Ensure lookups are case-insensitive by overwriting any existing entry
    # that matches the provided cid regardless of case.
    for key in list(log_names.keys()):
        if key.lower() == cid.lower():
            cid = key
            break
    log_names[cid] = name


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w.-]", "_", name)


def _file_path(cid: str) -> Path:
    name = log_names.get(cid, cid)
    return LOG_DIR / f"{_safe_name(name)}.log"


def add_log(cid: str, entry: str) -> None:
    """Append a log entry for the given charger id."""

    # Store log entries under the cid as provided but allow retrieval using
    # any casing by recording entries in a case-insensitive manner.
    key = next((k for k in logs.keys() if k.lower() == cid.lower()), cid)
    logs.setdefault(key, []).append(entry)
    path = _file_path(key)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(entry + "\n")


def get_logs(cid: str) -> list[str]:
    """Return all log entries for the given charger id."""

    # Try to find a matching log name case-insensitively
    name = log_names.get(cid)
    if name is None:
        for key, value in log_names.items():
            if key.lower() == cid.lower():
                cid = key
                name = value
                break
        else:
            try:
                from .models import Simulator

                sim = (
                    Simulator.objects.filter(cp_path__iexact=cid).first()
                )
                if sim:
                    cid = sim.cp_path
                    name = sim.name
                    log_names[cid] = name
            except Exception:  # pragma: no cover - best effort lookup
                pass

    path = _file_path(cid)
    if not path.exists():
        target = _safe_name(name or cid).lower()
        for file in LOG_DIR.glob("*.log"):
            if file.stem.lower() == target:
                path = file
                break

    if path.exists():
        return path.read_text(encoding="utf-8").splitlines()

    for key, entries in logs.items():
        if key.lower() == cid.lower():
            return entries
    return []


def clear_log(cid: str) -> None:
    """Remove any stored logs for the charger id."""

    key = next((k for k in list(logs.keys()) if k.lower() == cid.lower()), cid)
    logs.pop(key, None)
    path = _file_path(key)
    if not path.exists():
        target = _safe_name(log_names.get(key, key)).lower()
        for file in LOG_DIR.glob("*.log"):
            if file.stem.lower() == target:
                path = file
                break
    if path.exists():
        path.unlink()
