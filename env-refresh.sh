#!/usr/bin/env bash
set -e

VENV_DIR=".venv"

# Determine which Python executable is available
if command -v python3 >/dev/null 2>&1; then
  PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_CMD="python"
else
  echo "Python interpreter not found" >&2
  exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
  "$PYTHON_CMD" -m venv "$VENV_DIR"
fi

PYTHON="$VENV_DIR/bin/python"
if [ ! -f "$PYTHON" ]; then
  echo "Virtual environment not found" >&2
  exit 0
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

"$PYTHON" env-refresh.py database
