#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$BASE_DIR"

if [ ! -x "$BASE_DIR/.venv/bin/python" ]; then
  echo "Virtual environment not found. Run ./install.sh first." >&2
  exit 1
fi

exec "$BASE_DIR/.venv/bin/python" manage.py review_notify "$@"
