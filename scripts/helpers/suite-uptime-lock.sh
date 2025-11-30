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
