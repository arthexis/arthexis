#!/usr/bin/env bash
set -Eeuo pipefail

export DJANGO_SECRET_KEY="${DJANGO_SECRET_KEY:-mypy-secret-key}"
export ARTHEXIS_DISABLE_CELERY="${ARTHEXIS_DISABLE_CELERY:-1}"
export CELERY_LOG_LEVEL="${CELERY_LOG_LEVEL:-WARNING}"
export DEBUG="${DEBUG:-0}"
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PYTHON_BIN=".venv/bin/python"
if [[ "${OSTYPE:-}" == "msys" || "${OSTYPE:-}" == "cygwin" || "${OSTYPE:-}" == "win32" ]]; then
  PYTHON_BIN=".venv/Scripts/python.exe"
fi
emit_remediation() {
  local code="$1"
  local command="$2"
  local retry="$3"
  printf '{"code":"%s","command":"%s","event":"arthexis.qa.remediation","retry":"%s"}\n' "$code" "$command" "$retry" >&2
}

if [ ! -x "$PYTHON_BIN" ]; then
  emit_remediation "missing_venv_python" "./install.sh --terminal" "./scripts/run_mypy.sh"
  exit 1
fi

if ! "$PYTHON_BIN" -c "import importlib.util,sys;sys.exit(0 if importlib.util.find_spec('mypy') else 1)" >/dev/null 2>&1; then
  emit_remediation "missing_dependency" "./env-refresh.sh --deps-only" "./scripts/run_mypy.sh"
  exit 1
fi

"$SCRIPT_DIR/preflight-env.sh"

mypy_output="$(mktemp)"
cleanup() {
  rm -f "$mypy_output"
}
trap cleanup EXIT

"$PYTHON_BIN" -m mypy --config-file pyproject.toml "$@" >"$mypy_output" 2>&1 || status=$?
status="${status:-0}"

if ! "$PYTHON_BIN" - "$mypy_output" <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
patterns = [
    re.compile(r"^channel_layer\.(?:redis_url_invalid|fallback_inmemory)$"),
    re.compile(r"^\d{4}-\d{2}-\d{2} .* \[DEBUG\] celery\.utils\.functional:"),
    re.compile(r"^\d{4}-\d{2}-\d{2} .* \[DEBUG\] graphviz\._tools:"),
]

previous_blank = False
for line in path.read_text(encoding="utf-8").splitlines():
    if any(pattern.match(line) for pattern in patterns):
        continue
    is_blank = line.strip() == ""
    if is_blank and previous_blank:
        continue
    print(line)
    previous_blank = is_blank
PY
then
  filter_status=$?
  echo "Warning: MyPy output filter failed with exit code ${filter_status}; showing unfiltered output." >&2
  cat "$mypy_output" >&2
fi

exit "$status"
