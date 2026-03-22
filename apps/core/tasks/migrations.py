from __future__ import annotations

import json
from pathlib import Path

from .system_ops import _read_process_cmdline, _read_process_start_time


def _is_migration_server_process(cmdline: list[str], base_dir: Path) -> bool:
    """Return whether *cmdline* belongs to the migration server entrypoint.

    Args:
        cmdline: Raw process command-line parts.
        base_dir: Repository root used to resolve legacy wrapper paths.

    Returns:
        ``True`` when the process is running the migration server via either the
        preferred module entrypoint or the legacy wrapper script.
    """

    parts = [str(part) for part in cmdline]
    if not parts:
        return False

    legacy_script_path = base_dir / "scripts" / "migration_server.py"
    if any(part == str(legacy_script_path) for part in parts):
        return True

    return "utils.devtools.migration_server" in parts


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
    if not _is_migration_server_process(cmdline, lock_dir.parent):
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
