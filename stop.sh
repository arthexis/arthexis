#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"

# Activate virtual environment if present
if [ -d .venv ]; then
  source .venv/bin/activate
fi

ALL=false
PORT=8888

while [[ $# -gt 0 ]]; do
  case "$1" in
    --all)
      ALL=true
      shift
      ;;
    *)
      PORT="$1"
      shift
      ;;
  esac
done

if [ "$ALL" = true ]; then
  PATTERN="manage.py runserver"
else
  PATTERN="manage.py runserver 0.0.0.0:$PORT"
fi

pkill -f "$PATTERN" || true

while pgrep -f "$PATTERN" >/dev/null; do
  sleep 1
done
