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
CLEAN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --latest)
      LATEST=1
      shift
      ;;
    --clean)
      CLEAN=1
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


# Ensure pip is available; attempt to install if missing
if ! "$PYTHON" -m pip --version >/dev/null 2>&1; then
  echo "pip not found in virtual environment. Attempting to install with ensurepip..." >&2
  if "$PYTHON" -m ensurepip --upgrade >/dev/null 2>&1 && \
     "$PYTHON" -m pip --version >/dev/null 2>&1; then
    :
  else
    echo "Failed to install pip automatically. On Debian/Ubuntu/WSL, ensure python3-venv is installed and rerun ./install.sh." >&2
    exit 1
  fi
fi


if [ "$CLEAN" -eq 1 ]; then
  DB_FILE="$SCRIPT_DIR/db.sqlite3"
  if [ -f "$DB_FILE" ]; then
    BACKUP_DIR="$SCRIPT_DIR/backups"
    mkdir -p "$BACKUP_DIR"
    cp "$DB_FILE" "$BACKUP_DIR/db.sqlite3.$(date +%Y%m%d%H%M%S).bak"
  fi
  rm -f "$DB_FILE"
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

ARGS=""
if [ "$LATEST" -eq 1 ]; then
  ARGS="$ARGS --latest"
fi
if [ "$CLEAN" -eq 1 ]; then
  ARGS="$ARGS --clean"
fi
"$PYTHON" env-refresh.py $ARGS database
