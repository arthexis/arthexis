#!/usr/bin/env bash
# Run this script directly (ensure the executable bit is set).
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/helpers/env.sh
. "$BASE_DIR/scripts/helpers/env.sh"
# shellcheck source=scripts/helpers/logging.sh
. "$BASE_DIR/scripts/helpers/logging.sh"
# shellcheck source=scripts/helpers/ports.sh
. "$BASE_DIR/scripts/helpers/ports.sh"
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

is_port_reachable() {
  "${PYTHON:-python}" - "$1" <<'PY'
"""Return success when localhost:<port> accepts TCP connections."""
import socket
import sys

try:
    port = int(sys.argv[1])
except (IndexError, ValueError):
    raise SystemExit(1)

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.settimeout(1)
    try:
        sock.connect(("127.0.0.1", port))
    except OSError:
        raise SystemExit(1)

raise SystemExit(0)
PY
}

resolve_running_port() {
  local configured_port
  configured_port="$(arthexis_detect_backend_port "$BASE_DIR")"
  if is_port_reachable "$configured_port"; then
    printf '%s\n' "$configured_port"
    return 0
  fi

  local runserver_port
  runserver_port="$( (pgrep -af "manage.py runserver" || true) | sed -n \
    -e 's/.*runserver[[:space:]][^[:space:]]*:\([0-9]\{2,5\}\)\([[:space:]].*\|$\)/\1/p' \
    -e 's/.*runserver[[:space:]]\([0-9]\{2,5\}\)\([[:space:]].*\|$\)/\1/p' | head -n1)"
  if [ -n "$runserver_port" ] && is_port_reachable "$runserver_port"; then
    printf '%s\n' "$runserver_port"
    return 0
  fi

  return 1
}

parse_canonical_action() {
  # Mirror the legacy compatibility rules from utils.command_api.parse_legacy_args.
  local arg
  for arg in "$@"; do
    case "$arg" in
      -h|--help)
        printf 'help\n'
        return
        ;;
    esac
  done

  if [ "$#" -eq 0 ]; then
    printf 'list\n'
    return
  fi

  case "$1" in
    list|run)
      printf '%s\n' "$1"
      return
      ;;
  esac

  while [ "$#" -gt 0 ]; do
    case "$1" in
      --deprecated|--celery|--no-celery)
        shift
        ;;
      *)
        printf 'run\n'
        return
        ;;
    esac
  done

  printf 'list\n'
}

ACTION="$(parse_canonical_action "$@")"
if [ "$ACTION" = "run" ]; then
  if RUNNING_PORT="$(resolve_running_port)"; then
    export ARTHEXIS_RUNNING_PORT="$RUNNING_PORT"
    # Fast path: avoid command discovery/manage.py help when an instance is already up.
    export ARTHEXIS_COMMAND_FAST_RUN=1
  else
    echo "No running Arthexis instance detected. Start the app first (for example: ./start.sh)." >&2
    exit 1
  fi
fi

# Canonical interface:
#   Usage: arthexis cmd list [--deprecated] [--celery|--no-celery]
#   Usage: arthexis cmd run [--deprecated] [--celery|--no-celery] <django-command> [args...]
python -m utils.command_api "$@"
