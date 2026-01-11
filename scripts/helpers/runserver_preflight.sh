# shellcheck shell=bash

MIGRATIONS_SHA_FILE="${LOCK_DIR}/migrations.sha"

arthexis__resolve_python() {
  if [ -n "${ARTHEXIS_PYTHON_BIN:-}" ]; then
    return 0
  fi

  if command -v python3 >/dev/null 2>&1; then
    ARTHEXIS_PYTHON_BIN="python3"
    ARTHEXIS_PYTHON_ARGS=""
  elif command -v python >/dev/null 2>&1; then
    ARTHEXIS_PYTHON_BIN="python"
    ARTHEXIS_PYTHON_ARGS=""
  elif command -v py >/dev/null 2>&1; then
    ARTHEXIS_PYTHON_BIN="py"
    ARTHEXIS_PYTHON_ARGS="-3"
  else
    echo "Python is required to run migration preflight checks." >&2
    return 1
  fi

  return 0
}

compute_migration_fingerprint() {
  local base_dir
  base_dir="${1:-$BASE_DIR}"
  if [ -z "$base_dir" ]; then
    echo "" >&2
    return 1
  fi

  local python_cmd=()
  if ! arthexis__resolve_python; then
    return 1
  fi
  python_cmd=("$ARTHEXIS_PYTHON_BIN")
  if [ -n "${ARTHEXIS_PYTHON_ARGS:-}" ]; then
    python_cmd+=("$ARTHEXIS_PYTHON_ARGS")
  fi

  "${python_cmd[@]}" - "$base_dir" <<'PY'
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

run_runserver_preflight() {
  if [ "${RUNSERVER_PREFLIGHT_DONE:-false}" = true ]; then
    return 0
  fi

  local fingerprint
  if ! fingerprint=$(compute_migration_fingerprint); then
    echo "Failed to compute migration fingerprint" >&2
    return 1
  fi

  local python_cmd=()
  if ! arthexis__resolve_python; then
    echo "Failed to locate Python for migration checks" >&2
    return 1
  fi
  python_cmd=("$ARTHEXIS_PYTHON_BIN")
  if [ -n "${ARTHEXIS_PYTHON_ARGS:-}" ]; then
    python_cmd+=("$ARTHEXIS_PYTHON_ARGS")
  fi

  local stored_fingerprint=""
  if [ "${RUNSERVER_PREFLIGHT_FORCE_REFRESH:-false}" = true ]; then
    echo "Forcing migration preflight refresh..."
  elif [ -f "$MIGRATIONS_SHA_FILE" ]; then
    stored_fingerprint=$(cat "$MIGRATIONS_SHA_FILE")
  fi

  if [ "$stored_fingerprint" = "$fingerprint" ] && [ "${RUNSERVER_PREFLIGHT_FORCE_REFRESH:-false}" != true ]; then
    echo "Migrations unchanged since last successful preflight; verifying database state..."
    if "${python_cmd[@]}" manage.py migrate --check; then
      echo "Database matches cached migrations fingerprint; skipping migration checks."
      echo "$fingerprint" > "$MIGRATIONS_SHA_FILE"
      RUNSERVER_PREFLIGHT_DONE=true
      export DJANGO_SUPPRESS_MIGRATION_CHECK=1
      RUNSERVER_EXTRA_ARGS+=("--skip-checks")
      return 0
    fi

    echo "Cached migration fingerprint is stale; rerunning migration preflight..."
  fi

  echo "Inspecting migrations before runserver..."
  if migration_plan=$("${python_cmd[@]}" manage.py showmigrations --plan); then
    if echo "$migration_plan" | grep -q '^\s*\[ \]'; then
      echo "Applying pending migrations..."
      "${python_cmd[@]}" manage.py migrate --noinput
    else
      echo "No pending migrations detected; skipping migrate."
    fi
  else
    echo "Failed to inspect migrations" >&2
    return 1
  fi

  echo "Running Django migration check once before runserver..."
  "${python_cmd[@]}" manage.py migrate --check

  echo "$fingerprint" > "$MIGRATIONS_SHA_FILE"
  RUNSERVER_PREFLIGHT_DONE=true
  export DJANGO_SUPPRESS_MIGRATION_CHECK=1
  RUNSERVER_EXTRA_ARGS+=("--skip-checks")
}
