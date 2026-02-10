#!/usr/bin/env bash

arthexis__suite_uptime_python_bin() {
  local python_bin
  python_bin="$(command -v python3 || command -v python || true)"
  if [ -z "$python_bin" ]; then
    return 1
  fi
  echo "$python_bin"
  return 0
}

arthexis_refresh_suite_uptime_lock() {
  local base_dir="$1"
  if [ -z "$base_dir" ]; then
    return 0
  fi

  local python_bin
  if ! python_bin="$(arthexis__suite_uptime_python_bin)"; then
    return 0
  fi

  "$python_bin" - "$base_dir" <<'PY'
from __future__ import annotations
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

base_dir = Path(sys.argv[1])
lock_path = base_dir / ".locks" / "suite_uptime.lck"
lock_path.parent.mkdir(parents=True, exist_ok=True)
now = datetime.now(timezone.utc)
payload = {"started_at": now.isoformat()}
lock_path.write_text(json.dumps(payload), encoding="utf-8")
PY
}

arthexis_log_suite_uptime() {
  local base_dir="$1"
  if [ -z "$base_dir" ]; then
    return 0
  fi

  local python_bin
  if ! python_bin="$(arthexis__suite_uptime_python_bin)"; then
    return 0
  fi

  "$python_bin" - "$base_dir" <<'PY'
from __future__ import annotations
import sys
from datetime import datetime, timezone
from pathlib import Path


def _read_uptime_seconds(now: datetime) -> float | None:
    """Return the system uptime in seconds when available."""
    proc_path = Path("/proc/uptime")
    if proc_path.exists():
        try:
            return float(proc_path.read_text(encoding="utf-8").split()[0])
        except (OSError, ValueError, IndexError):
            return None
    try:
        import psutil
    except Exception:
        return None
    try:
        return max(0.0, now.timestamp() - float(psutil.boot_time()))
    except (OSError, ValueError):
        return None


base_dir = Path(sys.argv[1])
log_path = base_dir / "logs" / "suite-uptime.log"
log_path.parent.mkdir(parents=True, exist_ok=True)
now = datetime.now(timezone.utc)
uptime_seconds = _read_uptime_seconds(now)
uptime_value = "unknown" if uptime_seconds is None else f"{uptime_seconds:.2f}"
with log_path.open("a", encoding="utf-8") as handle:
    handle.write(f"{now.isoformat()} uptime_seconds={uptime_value}\n")
PY
}

arthexis_clear_suite_uptime_lock() {
  local base_dir="$1"
  if [ -z "$base_dir" ]; then
    return 0
  fi

  local python_bin
  if ! python_bin="$(arthexis__suite_uptime_python_bin)"; then
    return 0
  fi

  "$python_bin" - "$base_dir" <<'PY'
from __future__ import annotations
import json
import sys
from pathlib import Path

base_dir = Path(sys.argv[1])
lock_path = base_dir / ".locks" / "suite_uptime.lck"
lock_path.parent.mkdir(parents=True, exist_ok=True)
lock_path.write_text(json.dumps({}), encoding="utf-8")
PY
}
