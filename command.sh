#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/helpers/logging.sh
. "$BASE_DIR/scripts/helpers/logging.sh"
arthexis_resolve_log_dir "$BASE_DIR" LOG_DIR || exit 1
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1
cd "$BASE_DIR"

# Ensure virtual environment is available
if [ ! -d .venv ]; then
  echo "Virtual environment not found. Run ./install.sh first." >&2
  exit 1
fi
source .venv/bin/activate

celery_flag="--no-celery"

if [ $# -eq 0 ]; then
  echo "Available Django management commands:"
  python manage.py help --commands "$celery_flag"
  echo
  echo "Usage: $0 [--celery|--no-celery] <command> [args...]"
  exit 0
fi

case "$1" in
  --celery)
    celery_flag="--celery"
    shift
    ;;
  --no-celery)
    shift
    ;;
esac

if [ $# -eq 0 ]; then
  echo "Usage: $0 [--celery|--no-celery] <command> [args...]" >&2
  exit 1
fi

COMMAND="${1//-/_}"
shift

python manage.py "$COMMAND" "$@" "$celery_flag"
