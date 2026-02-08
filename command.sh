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

COMMAND_RAW="$1"
COMMAND="${COMMAND_RAW//-/_}"
shift

CACHE_DIR="$BASE_DIR/.cache"
CACHE_TTL_SECONDS="${ARTHEXIS_COMMAND_CACHE_TTL:-30}"
if ! [[ "$CACHE_TTL_SECONDS" =~ ^[0-9]+$ ]]; then
  CACHE_TTL_SECONDS=30
fi
CACHE_KEY="${celery_flag#--}"
CACHE_KEY="${CACHE_KEY//-/_}"
CACHE_FILE="$CACHE_DIR/command_list_${CACHE_KEY}.txt"

use_cache=false
if [ -f "$CACHE_FILE" ]; then
  now="$(date +%s)"
  cache_mtime="$(stat -c %Y "$CACHE_FILE" 2>/dev/null || echo 0)"
  cache_age="$((now - cache_mtime))"
  if [ "$cache_age" -lt "$CACHE_TTL_SECONDS" ]; then
    use_cache=true
  fi
fi

if [ "$use_cache" = "true" ]; then
  COMMAND_LIST="$(cat "$CACHE_FILE")"
else
  mkdir -p "$CACHE_DIR" 2>/dev/null || true
  cache_tmp=""
  if cache_tmp="$(mktemp "${CACHE_FILE}.XXXXXX" 2>/dev/null)"; then
    if python manage.py help --commands "$celery_flag" \
      | tr '\t' ' ' \
      | tr ' ' '\n' \
      | sed '/^$/d' \
      | grep -v '^\[.*]' \
      > "$cache_tmp"; then
      if mv "$cache_tmp" "$CACHE_FILE" 2>/dev/null; then
        COMMAND_LIST="$(cat "$CACHE_FILE")"
      else
        COMMAND_LIST="$(cat "$cache_tmp")"
        rm -f "$cache_tmp"
      fi
    else
      rm -f "$cache_tmp"
      exit 1
    fi
  else
    if ! COMMAND_LIST="$(
      python manage.py help --commands "$celery_flag" \
        | tr '\t' ' ' \
        | tr ' ' '\n' \
        | sed '/^$/d' \
        | grep -v '^\[.*]'
    )"; then
      exit 1
    fi
  fi
fi

if ! printf '%s\n' "$COMMAND_LIST" | awk -v cmd="$COMMAND" '($0 == cmd) { found = 1 } END { exit (found ? 0 : 1) }'; then
  MATCHES_PREFIX="$(
    printf '%s\n' "$COMMAND_LIST" | awk -v cmd="$COMMAND" 'index($0, cmd) == 1'
  )"
  MATCHES_CONTAINS="$(
    printf '%s\n' "$COMMAND_LIST" | awk -v cmd="$COMMAND" 'index($0, cmd) > 0 && index($0, cmd) != 1'
  )"

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
