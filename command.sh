#!/usr/bin/env bash
set -e

usage() {
  echo "Usage: $0 <command> [args...]"
}

case "${1:-}" in
  -h|--help)
    usage
    exit 0
    ;;
esac

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$BASE_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1
cd "$BASE_DIR"

# Ensure virtual environment is available
if [ ! -d .venv ]; then
  echo "Virtual environment not found. Run ./install.sh first." >&2
  exit 1
fi
source .venv/bin/activate

if [ $# -eq 0 ]; then
  usage >&2
  exit 1
fi

COMMAND="${1//-/_}"
shift

python manage.py "$COMMAND" "$@"
