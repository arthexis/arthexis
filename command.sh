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

load_command_list() {
  # Load and cache Django command names from manage.py help output.

  local use_cache=false
  if [ -f "$COMMAND_CACHE_FILE" ]; then
    local now
    local cache_mtime
    local cache_age
    now="$(date +%s)"
    cache_mtime="$(stat -c %Y "$COMMAND_CACHE_FILE" 2>/dev/null || echo 0)"
    cache_age="$((now - cache_mtime))"
    if [ "$cache_age" -lt "$CACHE_TTL_SECONDS" ]; then
      use_cache=true
    fi
  fi

  if [ "$use_cache" = "true" ]; then
    cat "$COMMAND_CACHE_FILE"
    return 0
  fi

  mkdir -p "$CACHE_DIR" 2>/dev/null || true
  local cache_tmp
  if cache_tmp="$(mktemp "${COMMAND_CACHE_FILE}.XXXXXX" 2>/dev/null)"; then
    if python manage.py help --commands "$celery_flag" \
      | tr '\t' ' ' \
      | tr ' ' '\n' \
      | sed '/^$/d' \
      | grep -v '^\[.*]' \
      > "$cache_tmp"; then
      if mv "$cache_tmp" "$COMMAND_CACHE_FILE" 2>/dev/null; then
        cat "$COMMAND_CACHE_FILE"
      else
        cat "$cache_tmp"
        rm -f "$cache_tmp"
      fi
      return 0
    fi

    rm -f "$cache_tmp"
    return 1
  fi

  python manage.py help --commands "$celery_flag" \
    | tr '\t' ' ' \
    | tr ' ' '\n' \
    | sed '/^$/d' \
    | grep -v '^\[.*]'
}

load_deprecated_absorbed_commands() {
  # Load absorbed/deprecated command names from command class decorators.

  local use_cache=false
  if [ -f "$DEPRECATED_CACHE_FILE" ]; then
    local now
    local cache_mtime
    local cache_age
    now="$(date +%s)"
    cache_mtime="$(stat -c %Y "$DEPRECATED_CACHE_FILE" 2>/dev/null || echo 0)"
    cache_age="$((now - cache_mtime))"
    if [ "$cache_age" -lt "$CACHE_TTL_SECONDS" ]; then
      use_cache=true
    fi
  fi

  if [ "$use_cache" = "true" ]; then
    cat "$DEPRECATED_CACHE_FILE"
    return 0
  fi

  mkdir -p "$CACHE_DIR" 2>/dev/null || true
  local cache_tmp
  if cache_tmp="$(mktemp "${DEPRECATED_CACHE_FILE}.XXXXXX" 2>/dev/null)"; then
    if python manage.py shell -c '
from django.core.management import get_commands, load_command_class
for command_name, app_name in sorted(get_commands().items()):
    try:
        command = load_command_class(app_name, command_name)
    except Exception:
        continue
    if getattr(command.__class__, "arthexis_absorbed_command", False):
        print(command_name)
' > "$cache_tmp"; then
      if mv "$cache_tmp" "$DEPRECATED_CACHE_FILE" 2>/dev/null; then
        cat "$DEPRECATED_CACHE_FILE"
      else
        cat "$cache_tmp"
        rm -f "$cache_tmp"
      fi
      return 0
    fi

    rm -f "$cache_tmp"
    return 1
  fi

  python manage.py shell -c '
from django.core.management import get_commands, load_command_class
for command_name, app_name in sorted(get_commands().items()):
    try:
        command = load_command_class(app_name, command_name)
    except Exception:
        continue
    if getattr(command.__class__, "arthexis_absorbed_command", False):
        print(command_name)
'
}

if ! COMMAND_LIST="$(load_command_list)"; then
  exit 1
fi

if [ "$show_deprecated" != "true" ]; then
  if ! DEPRECATED_COMMANDS="$(load_deprecated_absorbed_commands)"; then
    exit 1
  fi
  COMMAND_LIST="$({ printf '%s\n' "$COMMAND_LIST"; } | awk -v deprecated="$DEPRECATED_COMMANDS" '
    BEGIN {
      split(deprecated, values, "\n")
      for (i in values) {
        if (values[i] != "") {
          blocked[values[i]] = 1
        }
      }
    }
    !blocked[$0]
  ')"
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
