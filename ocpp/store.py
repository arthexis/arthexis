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

    log_names[cid] = name


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w.-]", "_", name)


def _file_path(cid: str) -> Path:
    name = log_names.get(cid, cid)
    return LOG_DIR / f"{_safe_name(name)}.log"


def add_log(cid: str, entry: str) -> None:
    """Append a log entry for the given charger id."""

    logs.setdefault(cid, []).append(entry)
    path = _file_path(cid)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(entry + "\n")


def get_logs(cid: str) -> list[str]:
    """Return all log entries for the given charger id."""

    if cid not in log_names:
        try:
            from .models import Simulator

            sim = Simulator.objects.filter(cp_path=cid).first()
            if sim:
                log_names[cid] = sim.name
        except Exception:  # pragma: no cover - best effort lookup
            pass
    path = _file_path(cid)
    if path.exists():
        return path.read_text(encoding="utf-8").splitlines()
    return logs.get(cid, [])


def clear_log(cid: str) -> None:
    """Remove any stored logs for the charger id."""

    logs.pop(cid, None)
    path = _file_path(cid)
    if path.exists():
        path.unlink()
