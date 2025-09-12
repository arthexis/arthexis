#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$BASE_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1
cd "$BASE_DIR"

if [ ! -d .venv ]; then
  echo "Virtual environment not found. Run ./install.sh first." >&2
  exit 1
fi
source .venv/bin/activate

python -m gway "$@"
