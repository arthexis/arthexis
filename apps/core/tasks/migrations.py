from __future__ import annotations

import json
from pathlib import Path

from .system_ops import _read_process_cmdline, _read_process_start_time


def _is_migration_server_running(lock_dir: Path) -> bool:
    """Return ``True`` when the migration server lock indicates it is active."""

    state_path = lock_dir / "migration_server.json"
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except json.JSONDecodeError:
        return True

    pid = payload.get("pid")
    if isinstance(pid, str) and pid.isdigit():
        pid = int(pid)
    if not isinstance(pid, int):
        return False

    cmdline = _read_process_cmdline(pid)
    script_path = lock_dir.parent / "scripts" / "migration_server.py"
    if not any(str(part) == str(script_path) for part in cmdline):
        return False

    timestamp = payload.get("timestamp")
    if isinstance(timestamp, str):
        try:
            timestamp = float(timestamp)
        except ValueError:
            timestamp = None

    start_time = _read_process_start_time(pid)
    if (
        isinstance(timestamp, (int, float))
        and start_time is not None
        and abs(start_time - timestamp) > 120
    ):
        return False

    return True
