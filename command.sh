#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"

# Activate virtual environment if present
if [ -d .venv ]; then
  source .venv/bin/activate
fi

if [ $# -eq 0 ]; then
  echo "Usage: $0 <command> [args...]" >&2
  exit 1
fi

COMMAND="${1//-/_}"
shift

python manage.py "$COMMAND" "$@"
