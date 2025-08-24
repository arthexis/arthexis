#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"

# Ensure virtual environment is available
if [ ! -d .venv ]; then
  echo "Virtual environment not found. Run ./install.sh first." >&2
  exit 1
fi
source .venv/bin/activate

if [ $# -eq 0 ]; then
  echo "Usage: $0 <command> [args...]" >&2
  exit 1
fi

COMMAND="${1//-/_}"
shift

python manage.py "$COMMAND" "$@"
