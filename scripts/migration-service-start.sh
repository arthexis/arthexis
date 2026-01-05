#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
export TZ="${TZ:-America/Monterrey}"
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings}"
# shellcheck source=scripts/helpers/logging.sh
. "$BASE_DIR/scripts/helpers/logging.sh"

arthexis_resolve_log_dir "$BASE_DIR" LOG_DIR || exit 1
LOG_FILE="$LOG_DIR/migration-service.log"
exec > >(tee -a "$LOG_FILE") 2>&1
cd "$BASE_DIR"

if [ ! -d .venv ]; then
  echo "Virtual environment not found. Run ./install.sh first." >&2
  exit 1
fi

source .venv/bin/activate

for env_file in *.env; do
  [ -f "$env_file" ] || continue
  set -a
  . "$env_file"
  set +a
done

python "$BASE_DIR/scripts/migration_server.py" --latest --debounce 1
