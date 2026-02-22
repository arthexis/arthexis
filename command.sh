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
arthexis_secure_log_file "$LOG_DIR" "$0" LOG_FILE || exit 1
exec > >(tee "$LOG_FILE") 2>&1
cd "$BASE_DIR"

# Ensure virtual environment is available
if [ ! -d .venv ]; then
  echo "Virtual environment not found. Run ./install.sh first." >&2
  exit 1
fi
source .venv/bin/activate

celery_flag="--no-celery"
show_deprecated=false

while [ $# -gt 0 ]; do
  case "$1" in
    --celery)
      celery_flag="--celery"
      shift
      ;;
    --no-celery)
      shift
      ;;
    --deprecated)
      show_deprecated=true
      shift
      ;;
    *)
      break
      ;;
  esac
done

CACHE_DIR="$BASE_DIR/.cache"
CACHE_TTL_SECONDS="${ARTHEXIS_COMMAND_CACHE_TTL:-30}"
if ! [[ "$CACHE_TTL_SECONDS" =~ ^[0-9]+$ ]]; then
  CACHE_TTL_SECONDS=30
fi
CACHE_KEY="${celery_flag#--}"
CACHE_KEY="${CACHE_KEY//-/_}"
COMMAND_CACHE_FILE="$CACHE_DIR/command_list_${CACHE_KEY}.txt"
DEPRECATED_CACHE_FILE="$CACHE_DIR/deprecated_absorbed_commands.txt"
DEPRECATED_COMMAND_DISCOVERY_SCRIPT='
from django.core.management import get_commands
from importlib import import_module
for command_name, app_name in sorted(get_commands().items()):
    try:
        module = import_module(f"{app_name}.management.commands.{command_name}")
        cls = module.Command
    except Exception:
        continue
    if getattr(cls, "arthexis_absorbed_command", False):
        print(command_name)
'

cache_mtime_seconds() {
  # Return cache file mtime in epoch seconds across GNU/BSD stat variants.
  local cache_file="$1"
  stat -c %Y "$cache_file" 2>/dev/null || stat -f %m "$cache_file" 2>/dev/null || echo 0
}

cached_command_output() {
  # Usage: cached_command_output <cache_file> <command...>
  local cache_file="$1"
  shift

  local use_cache=false
  if [ -f "$cache_file" ]; then
    local now
    local cache_mtime
    local cache_age
    now="$(date +%s)"
    cache_mtime="$(cache_mtime_seconds "$cache_file")"
    cache_age="$((now - cache_mtime))"
    if [ "$cache_age" -lt "$CACHE_TTL_SECONDS" ]; then
      use_cache=true
    fi
  fi

  if [ "$use_cache" = "true" ]; then
    cat "$cache_file"
    return 0
  fi

  mkdir -p "$CACHE_DIR" 2>/dev/null || true
  local cache_tmp
  if cache_tmp="$(mktemp "${cache_file}.XXXXXX" 2>/dev/null)"; then
    if "$@" > "$cache_tmp"; then
      if mv "$cache_tmp" "$cache_file" 2>/dev/null; then
        cat "$cache_file"
      else
        cat "$cache_tmp"
        rm -f "$cache_tmp"
      fi
      return 0
    fi

    rm -f "$cache_tmp"
    return 1
  fi

  "$@"
}

load_command_list() {
  # Load and cache Django command names from manage.py help output.

  cached_command_output "$COMMAND_CACHE_FILE" \
    bash -c "set -o pipefail; python manage.py help --commands '$celery_flag' \
      | tr '\t' ' ' \
      | tr ' ' '\n' \
      | sed '/^$/d' \
      | grep -v '^\\[.*]' \
      | awk '/^[a-z0-9][a-z0-9_-]*$/'"
}

load_deprecated_absorbed_commands() {
  # Load absorbed/deprecated command names from command class decorators.

  cached_command_output "$DEPRECATED_CACHE_FILE" \
    python manage.py shell -c "$DEPRECATED_COMMAND_DISCOVERY_SCRIPT"
}

if ! COMMAND_LIST="$(load_command_list)"; then
  exit 1
fi
if [ -z "$COMMAND_LIST" ]; then
  echo "Command discovery returned no results. Check Django configuration." >&2
  exit 1
fi

if [ "$show_deprecated" != "true" ]; then
  if ! DEPRECATED_COMMANDS="$(load_deprecated_absorbed_commands)"; then
    exit 1
  fi
  COMMAND_LIST="$(awk 'NR==FNR { if ($0 != "") blocked[$0] = 1; next } !blocked[$0]' \
    <(printf '%s\n' "$DEPRECATED_COMMANDS") \
    <(printf '%s\n' "$COMMAND_LIST"))"
fi

if [ $# -eq 0 ]; then
  echo "Available Django management commands:"
  printf '%s\n' "$COMMAND_LIST"
  echo
  echo "Usage: $0 [--celery|--no-celery] [--deprecated] <command> [args...]"
  exit 0
fi

COMMAND_RAW="$1"
COMMAND="${COMMAND_RAW//-/_}"
shift

if ! printf '%s\n' "$COMMAND_LIST" | awk -v cmd="$COMMAND" '($0 == cmd) { found = 1 } END { exit (found ? 0 : 1) }'; then
  MATCHES_PREFIX="$({ printf '%s\n' "$COMMAND_LIST"; } | awk -v cmd="$COMMAND" 'index($0, cmd) == 1')"
  MATCHES_CONTAINS="$({ printf '%s\n' "$COMMAND_LIST"; } | awk -v cmd="$COMMAND" 'index($0, cmd) > 0 && index($0, cmd) != 1')"

  echo "No exact match for '$COMMAND_RAW'." >&2
  if [ -n "$MATCHES_PREFIX" ] || [ -n "$MATCHES_CONTAINS" ]; then
    echo "Possible commands:" >&2
    if [ -n "$MATCHES_PREFIX" ]; then
      printf '%s\n' "$MATCHES_PREFIX" | sed 's/^/  /' >&2
    fi
    if [ -n "$MATCHES_CONTAINS" ]; then
      printf '%s\n' "$MATCHES_CONTAINS" | sed 's/^/  /' >&2
    fi
  else
    echo "Run '$0' with no arguments to see available commands." >&2
  fi
  exit 1
fi

python manage.py "$COMMAND" "$@" "$celery_flag"
