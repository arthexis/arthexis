#!/usr/bin/env bash
set -e

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
