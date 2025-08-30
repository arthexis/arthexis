#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1

VENV_DIR=".venv"
PYTHON="$VENV_DIR/bin/python"

LATEST=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --latest)
      LATEST=1
      shift
      ;;
    *)
      break
      ;;
  esac
done

if [ ! -f "$PYTHON" ]; then
  echo "Virtual environment not found. Run ./install.sh first." >&2
  exit 1
fi

RUNNING=0
if pgrep -f "manage.py runserver" >/dev/null 2>&1; then
  RUNNING=1
  "$SCRIPT_DIR/stop.sh" --all >/dev/null 2>&1 || true
fi

if [ -f requirements.txt ]; then
  REQ_FILE="requirements.txt"
  MD5_FILE="requirements.md5"
  NEW_HASH=$(md5sum "$REQ_FILE" | awk '{print $1}')
  STORED_HASH=""
  [ -f "$MD5_FILE" ] && STORED_HASH=$(cat "$MD5_FILE")
  if [ "$NEW_HASH" != "$STORED_HASH" ]; then
    "$PYTHON" -m pip install -r "$REQ_FILE"
    echo "$NEW_HASH" > "$MD5_FILE"
  fi
fi

if [ "$LATEST" -eq 1 ]; then
  "$PYTHON" env-refresh.py --latest database
else
  "$PYTHON" env-refresh.py database
fi

if [ "$RUNNING" -eq 1 ]; then
  nohup "$SCRIPT_DIR/start.sh" --reload >/dev/null 2>&1 &
fi
