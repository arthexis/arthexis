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
# shellcheck source=scripts/helpers/service_manager.sh
. "$BASE_DIR/scripts/helpers/service_manager.sh"
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

is_systemd_unit_active() {
  local unit_name="$1"
  if [ -z "$unit_name" ] || ! command -v systemctl >/dev/null 2>&1; then
    return 1
  fi

  if command -v sudo >/dev/null 2>&1 && [ "$(id -u)" -ne 0 ]; then
    if sudo -n systemctl is-active --quiet "$unit_name" 2>/dev/null; then
      return 0
    fi
  fi

  systemctl is-active --quiet "$unit_name" 2>/dev/null
}

has_active_arthexis_instance() {
  local lock_dir="$BASE_DIR/.locks"
  local service_lock="$lock_dir/service.lck"
  if [ ! -f "$service_lock" ]; then
    return 1
  fi

  local service_name
  service_name="$(tr -d '\r\n[:space:]' < "$service_lock")"
  if [ -z "$service_name" ]; then
    return 1
  fi

  local unit_name="${service_name}.service"
  if ! _arthexis_systemd_unit_present "$unit_name"; then
    return 1
  fi

  is_systemd_unit_active "$unit_name"
}

resolve_running_port() {
  local configured_port
  configured_port="$(arthexis_detect_backend_port "$BASE_DIR")"
  if is_port_reachable "$configured_port"; then
    printf '%s\n' "$configured_port"
    return 0
  fi

  local runserver_port
  runserver_port="$(arthexis_detect_live_runserver_port "$BASE_DIR" || true)"
  if [ -n "$runserver_port" ] && is_port_reachable "$runserver_port"; then
    printf '%s\n' "$runserver_port"
    return 0
  fi

  if has_active_arthexis_instance; then
    # Systemd-managed deployments may be active before local HTTP probing
    # succeeds (for example, while workers are still booting).
    printf '%s\n' "$configured_port"
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
