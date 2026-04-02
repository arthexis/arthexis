#!/usr/bin/env bash
# Run this script directly (ensure the executable bit is set).
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/helpers/env.sh
. "$BASE_DIR/scripts/helpers/env.sh"
# shellcheck source=scripts/helpers/logging.sh
. "$BASE_DIR/scripts/helpers/logging.sh"
arthexis_load_env_file "$BASE_DIR"
arthexis_resolve_log_dir "$BASE_DIR" LOG_DIR || exit 1
LOG_FILE="$LOG_DIR/command.log"
if [[ -L "$LOG_FILE" ]]; then
  echo "Refusing to write to symlinked log file: $LOG_FILE" >&2
  exit 1
fi

LOG_FILE_CREATED=0
if [[ ! -e "$LOG_FILE" ]]; then
  umask 077
  : > "$LOG_FILE"
  LOG_FILE_CREATED=1
fi

if [[ -L "$LOG_FILE" ]]; then
  if [[ "$LOG_FILE_CREATED" -eq 1 ]]; then
    rm -f -- "$LOG_FILE"
  fi
  echo "Refusing to write to symlinked log file: $LOG_FILE" >&2
  exit 1
fi

chmod 600 "$LOG_FILE"
exec > >(tee -a "$LOG_FILE") 2>&1
# Keep command logging minimal to avoid exposing sensitive arguments in logs.
printf "%s command.sh invocation" "$(date -Iseconds)"
if [[ $# -gt 0 ]]; then
  printf " command=%q" "$1"
fi
echo " args=<redacted>"
cd "$BASE_DIR"

# Ensure virtual environment is available
if [ ! -d .venv ]; then
  echo "Virtual environment not found. Run ./install.sh first." >&2
  exit 1
fi
source .venv/bin/activate

# Supported interface:
#   Usage: ./command.sh <operational-command> [args...]
#   Usage: ./command.sh list
# For non-operational/admin commands, use ./manage.py directly.
python -m utils.command_api "$@"
