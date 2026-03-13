# shellcheck shell=bash

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

if [ -z "${BASE_DIR:-}" ]; then
  BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi

LOCK_DIR="${LOCK_DIR:-${BASE_DIR}/.locks}"
LOCK_DIR="$(normalize_path "$LOCK_DIR")"

MIGRATIONS_SHA_FILE="${LOCK_DIR}/migrations.sha"
PREDEPLOY_MIGRATIONS_MARKER_FILE="${LOCK_DIR}/predeploy_migrate_success.json"

default_migration_policy() {
  local role="${NODE_ROLE:-}"

  if [ -z "$role" ] && [ -n "${LOCK_DIR:-}" ] && [ -f "${LOCK_DIR}/role.lck" ]; then
    role="$(cat "${LOCK_DIR}/role.lck")"
  fi

  role="${role//[[:space:]]/}"

  case "${role,,}" in
    satellite|watchtower)
      echo "check"
      ;;
    *)
      echo "apply"
      ;;
  esac
}

resolve_migration_policy() {
  local configured_policy="${ARTHEXIS_MIGRATION_POLICY:-}"

  if [ -z "$configured_policy" ]; then
    default_migration_policy
    return 0
  fi

  case "${configured_policy,,}" in
    apply|check|skip)
      echo "${configured_policy,,}"
      ;;
    *)
      echo "Unsupported ARTHEXIS_MIGRATION_POLICY value '${configured_policy}'. Expected one of: apply, check, skip." >&2
      return 1
      ;;
  esac
}

compute_migration_fingerprint() {
  local base_dir
  local python_bin
  base_dir="${1:-${BASE_DIR:-$(pwd)}}"
  base_dir="$(normalize_path "$base_dir")"
  if [ -z "$base_dir" ]; then
    echo "" >&2
    return 1
  fi

  if ! python_bin="$(arthexis_python_bin)"; then
    echo "python3 or python not available" >&2
    return 1
  fi

  "$python_bin" - "$base_dir" <<'PY'
import hashlib
import pathlib
import sys

base = pathlib.Path(sys.argv[1])
paths = sorted(base.glob("apps/**/migrations/*.py"))

hasher = hashlib.sha256()
for path in paths:
    if not path.is_file():
        continue
    hasher.update(str(path.relative_to(base)).encode())
    hasher.update(path.read_bytes())

print(hasher.hexdigest())
PY
}

read_predeploy_marker_fingerprint() {
  local marker_file="${1:-$PREDEPLOY_MIGRATIONS_MARKER_FILE}"
  local python_bin

  if [ ! -f "$marker_file" ]; then
    return 1
  fi

  if ! python_bin="$(arthexis_python_bin)"; then
    return 1
  fi

  "$python_bin" - "$marker_file" <<'PY'
import json
import pathlib
import sys

marker = pathlib.Path(sys.argv[1])
try:
    payload = json.loads(marker.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    raise SystemExit(1)

if payload.get("status") != "success":
    raise SystemExit(1)

fingerprint = payload.get("fingerprint")
if not isinstance(fingerprint, str) or not fingerprint:
    raise SystemExit(1)

print(fingerprint)
PY
}

run_runserver_preflight() {
  if [ "${RUNSERVER_PREFLIGHT_DONE:-false}" = true ]; then
    return 0
  fi

  local python_bin
  if ! python_bin="$(arthexis_python_bin)"; then
    echo "python3 or python not available" >&2
    return 1
  fi

  local migration_policy
  if ! migration_policy="$(resolve_migration_policy)"; then
    return 1
  fi

  write_migration_fingerprint() {
    local value="$1"

    if ! printf '%s\n' "$value" > "$MIGRATIONS_SHA_FILE"; then
      echo "Failed to write migrations fingerprint cache '$MIGRATIONS_SHA_FILE'." >&2
      return 1
    fi

    return 0
  }

  if [ "$migration_policy" = "skip" ]; then
    echo "Skipping runserver migration preflight (ARTHEXIS_MIGRATION_POLICY=skip)."
    RUNSERVER_PREFLIGHT_DONE=true
    return 0
  fi

  local fingerprint
  if ! fingerprint=$(compute_migration_fingerprint); then
    echo "Failed to compute migration fingerprint" >&2
    return 1
  fi

  if ! mkdir -p "$LOCK_DIR"; then
    echo "Failed to create lock directory '$LOCK_DIR'." >&2
    return 1
  fi

  local stored_fingerprint=""
  if [ "${RUNSERVER_PREFLIGHT_FORCE_REFRESH:-false}" = true ]; then
    echo "Forcing migration preflight refresh..."
  elif [ -f "$MIGRATIONS_SHA_FILE" ]; then
    stored_fingerprint=$(cat "$MIGRATIONS_SHA_FILE")
  fi

  local marker_fingerprint=""
  if [ "${RUNSERVER_PREFLIGHT_FORCE_REFRESH:-false}" != true ] && marker_fingerprint=$(read_predeploy_marker_fingerprint); then
    if [ "$marker_fingerprint" = "$fingerprint" ]; then
      echo "Found successful pre-deploy migration marker; verifying migration state..."
      if "$python_bin" manage.py migrate --check; then
        echo "Pre-deploy migration marker verified; skipping migration apply fallback."
        if ! write_migration_fingerprint "$fingerprint"; then
          return 1
        fi
        RUNSERVER_PREFLIGHT_DONE=true
        export DJANGO_SUPPRESS_MIGRATION_CHECK=1
        RUNSERVER_EXTRA_ARGS+=("--skip-checks")
        return 0
      fi

      echo "Pre-deploy migration marker did not verify cleanly; running fallback migration preflight..."
    fi
  fi

  if [ "$stored_fingerprint" = "$fingerprint" ] && [ "${RUNSERVER_PREFLIGHT_FORCE_REFRESH:-false}" != true ]; then
    echo "Migrations unchanged since last successful preflight; verifying database state..."
    if "$python_bin" manage.py migrate --check; then
      echo "Database matches cached migrations fingerprint; skipping migration checks."
      if ! write_migration_fingerprint "$fingerprint"; then
        return 1
      fi
      RUNSERVER_PREFLIGHT_DONE=true
      export DJANGO_SUPPRESS_MIGRATION_CHECK=1
      RUNSERVER_EXTRA_ARGS+=("--skip-checks")
      return 0
    fi

    echo "Cached migration fingerprint is stale; rerunning migration preflight..."
  fi

  local migrate_check_output=""
  local migrate_check_status=0

  run_migrate_check() {
    if migrate_check_output=$("$python_bin" manage.py migrate --check 2>&1); then
      migrate_check_status=0
    else
      migrate_check_status=$?
    fi

    if [ "$migrate_check_status" -eq 0 ]; then
      return 0
    fi

    return 10
  }

  echo "Checking for unapplied migrations before runserver..."
  if run_migrate_check; then
    echo "No pending migrations detected; skipping migrate."
  else
    migrate_check_status=$?
    if [ "$migrate_check_status" -ne 10 ]; then
      return 1
    fi

    if [ "$migration_policy" = "check" ]; then
      echo "Migration preflight failed: pending migrations detected and policy is check-only." >&2
      printf '%s\n' "$migrate_check_output" >&2
      return 1
    fi

    echo "Pending migrations detected; applying migrations..."
    if ! "$python_bin" manage.py migrate --noinput; then
      echo "Migration preflight failed while applying migrations." >&2
      return 1
    fi

    echo "Verifying migration state after applying migrations..."
    if ! "$python_bin" manage.py migrate --check; then
      echo "Migration preflight failed: migrations are still pending after apply." >&2
      return 1
    fi
  fi

  if ! write_migration_fingerprint "$fingerprint"; then
    return 1
  fi
  RUNSERVER_PREFLIGHT_DONE=true
  export DJANGO_SUPPRESS_MIGRATION_CHECK=1
  RUNSERVER_EXTRA_ARGS+=("--skip-checks")
  return 0
}
