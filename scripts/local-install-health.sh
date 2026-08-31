#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEFAULT_TARGET="py312-sqlite-smoke"
TARGETS=()
INSTALL_SYSTEM_DEPS=0
CLEAN=0
USER_POSTGRES_DB="${POSTGRES_DB:-}"

usage() {
  cat <<'USAGE'
Usage: ./scripts/local-install-health.sh [--target TARGET | --all] [--clean] [--install-system-deps]

Run the Install Health Check workflow locally from a Linux/WSL checkout without
using GitHub-hosted Actions minutes.

Options:
  --target TARGET        Run one target. Can be provided more than once.
  --all                  Run all install-health targets.
  --list                 Print available targets.
  --clean                Remove the cached virtualenv for each selected target first.
  --install-system-deps  Install the base Ubuntu packages used by CI before running.
  -h, --help             Show this help.

Targets:
  py312-sqlite-smoke
  py312-postgres-smoke
  py311-sqlite-ocpp
  py311-sqlite-rest
  py311-postgres-smoke

The script uses $XDG_CACHE_HOME/arthexis/local-install-health/<checkout> by
default for target-specific virtualenvs and SQLite databases so the checkout
.venv and db.sqlite3 stay untouched. Override that cache root with
ARTHEXIS_LOCAL_INSTALL_HEALTH_ENV_ROOT.
USAGE
}

all_targets() {
  printf '%s\n' \
    py312-sqlite-smoke \
    py312-postgres-smoke \
    py311-sqlite-ocpp \
    py311-sqlite-rest \
    py311-postgres-smoke
}

target_config() {
  local target="$1"

  TARGET_PYTHON_VERSION=""
  TARGET_DB_BACKEND=""
  TARGET_SHARD=""
  TARGET_FULL_PYTEST=0
  TARGET_PYTEST_ARGS=()

  case "$target" in
    py312-sqlite-smoke)
      TARGET_PYTHON_VERSION="3.12"
      TARGET_DB_BACKEND="sqlite"
      TARGET_SHARD="smoke"
      ;;
    py312-postgres-smoke)
      TARGET_PYTHON_VERSION="3.12"
      TARGET_DB_BACKEND="postgres"
      TARGET_SHARD="smoke"
      ;;
    py311-sqlite-ocpp)
      TARGET_PYTHON_VERSION="3.11"
      TARGET_DB_BACKEND="sqlite"
      TARGET_SHARD="ocpp"
      TARGET_FULL_PYTEST=1
      TARGET_PYTEST_ARGS=(apps/ocpp/tests)
      ;;
    py311-sqlite-rest)
      TARGET_PYTHON_VERSION="3.11"
      TARGET_DB_BACKEND="sqlite"
      TARGET_SHARD="rest"
      TARGET_FULL_PYTEST=1
      TARGET_PYTEST_ARGS=(--ignore=apps/ocpp/tests)
      ;;
    py311-postgres-smoke)
      TARGET_PYTHON_VERSION="3.11"
      TARGET_DB_BACKEND="postgres"
      TARGET_SHARD="smoke"
      ;;
    *)
      echo "Unknown install-health target: $target" >&2
      echo "Run ./scripts/local-install-health.sh --list to see valid targets." >&2
      return 1
      ;;
  esac
}

python_matches_version() {
  local python_bin="$1"
  local expected="$2"

  "$python_bin" - "$expected" <<'PY' >/dev/null 2>&1
import sys

expected = sys.argv[1]
actual = f"{sys.version_info.major}.{sys.version_info.minor}"
raise SystemExit(0 if actual == expected else 1)
PY
}

find_python_for_version() {
  local version="$1"
  local env_name="ARTHEXIS_PYTHON_${version//./_}_BIN"
  local candidate
  local candidates=(
    "${!env_name:-}"
    "${ARTHEXIS_PYTHON_BIN:-}"
    "python${version}"
    "python${version/./}"
    python3
    python
  )

  for candidate in "${candidates[@]}"; do
    if [[ -z "$candidate" ]] || ! command -v "$candidate" >/dev/null 2>&1; then
      continue
    fi
    candidate="$(command -v "$candidate")"
    if python_matches_version "$candidate" "$version"; then
      printf '%s' "$candidate"
      return 0
    fi
  done

  return 1
}

resolve_venv_dir() {
  "$ARTHEXIS_PYTHON_BIN" "$REPO_ROOT/scripts/helpers/venv_path.py" "$REPO_ROOT" --include-ci
}

install_base_system_deps() {
  local sudo_cmd=()

  if [[ "$(id -u)" != "0" ]]; then
    if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
      sudo_cmd=(sudo -n)
    else
      echo "Passwordless sudo is required for --install-system-deps." >&2
      return 1
    fi
  fi

  "${sudo_cmd[@]}" apt-get update
  "${sudo_cmd[@]}" apt-get install -y --no-install-recommends \
    ca-certificates curl git sudo python3 python3-venv \
    libcairo2 libgdk-pixbuf-2.0-0 libpango-1.0-0 \
    libpangocairo-1.0-0 shared-mime-info
}

safe_target_name() {
  local target="$1"
  printf '%s' "${target//-/_}"
}

run_target() {
  local target="$1"
  local python_bin
  local safe_target
  local target_started_at
  local docs_admin_password
  local venv_dir
  local xdist_args=()

  target_config "$target"
  safe_target="$(safe_target_name "$target")"
  target_started_at="$(date +%s)"

  echo "==> local install health: $target"
  echo "    python=${TARGET_PYTHON_VERSION} db=${TARGET_DB_BACKEND} shard=${TARGET_SHARD}"

  if ! python_bin="$(find_python_for_version "$TARGET_PYTHON_VERSION")"; then
    echo "Python ${TARGET_PYTHON_VERSION} is required for ${target} but was not found." >&2
    echo "Install it or choose a target matching the local Python runtime." >&2
    return 1
  fi

  export ARTHEXIS_DB_BACKEND="$TARGET_DB_BACKEND"
  export ARTHEXIS_ENV_ROOT="$CACHE_ROOT"
  export ARTHEXIS_INCLUDE_QA_REQUIREMENTS=1
  export ARTHEXIS_PYTHON_BIN="$python_bin"
  export NODE_OPTIONS="${NODE_OPTIONS:---no-deprecation}"
  export OCPP_STATE_REDIS_URL="${OCPP_STATE_REDIS_URL:-redis://localhost:6379}"
  export PIP_DEFAULT_TIMEOUT="${PIP_DEFAULT_TIMEOUT:-60}"
  export PIP_RETRIES="${PIP_RETRIES:-5}"
  export PYTHONDONTWRITEBYTECODE="${PYTHONDONTWRITEBYTECODE:-1}"
  export PYTEST_WORKERS="${PYTEST_WORKERS:-auto}"
  export REDIS_HOST="${REDIS_HOST:-127.0.0.1}"
  export REDIS_PORT="${REDIS_PORT:-6379}"

  mkdir -p "$CACHE_ROOT/db" "$CACHE_ROOT/test-db"
  if [[ "$TARGET_DB_BACKEND" == "sqlite" ]]; then
    export ARTHEXIS_SQLITE_PATH="$CACHE_ROOT/db/${safe_target}.sqlite3"
    export ARTHEXIS_SQLITE_TEST_PATH="$CACHE_ROOT/test-db/${safe_target}.sqlite3"
  else
    unset ARTHEXIS_SQLITE_PATH ARTHEXIS_SQLITE_TEST_PATH
  fi

  export POSTGRES_DB="${USER_POSTGRES_DB:-arthexis_${safe_target}}"
  export POSTGRES_HOST="${POSTGRES_HOST:-127.0.0.1}"
  export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-postgres}"
  export POSTGRES_PORT="${POSTGRES_PORT:-5432}"
  export POSTGRES_USER="${POSTGRES_USER:-postgres}"

  "$ARTHEXIS_PYTHON_BIN" --version

  unset ARTHEXIS_VENV_DIR
  venv_dir="$(resolve_venv_dir)"
  export ARTHEXIS_VENV_DIR="$venv_dir"
  if [[ "$CLEAN" == "1" ]]; then
    case "$venv_dir" in
      "$CACHE_ROOT"/venvs/*)
        rm -rf "$venv_dir"
        ;;
      *)
        echo "Refusing to clean virtualenv outside local install-health cache: $venv_dir" >&2
        return 1
        ;;
    esac
  fi

  if [[ "$INSTALL_SYSTEM_DEPS" == "1" ]]; then
    install_base_system_deps
  fi

  ./scripts/ci/start-native-redis.sh
  ./scripts/ci/start-native-postgres.sh

  "$ARTHEXIS_PYTHON_BIN" "$REPO_ROOT/scripts/sort_pyproject_deps.py" --check
  "$ARTHEXIS_PYTHON_BIN" "$REPO_ROOT/scripts/generate_requirements.py" --check

  ./install.sh --embedded --no-start
  ./env-refresh.sh

  # shellcheck source=/dev/null
  source "$venv_dir/bin/activate"

  ./scripts/preflight-env.sh
  python -m pip install --only-binary=:all: -r requirements-ci.txt
  python scripts/check_editable_install_import.py

  ./scripts/preflight-env.sh
  python scripts/check_migration_conflicts.py
  python manage.py migrations check
  python manage.py migrate --noinput --database default

  if [[ -n "${DOCS_ADMIN_PASSWORD:-}" ]]; then
    docs_admin_password="$DOCS_ADMIN_PASSWORD"
  elif command -v openssl >/dev/null 2>&1; then
    docs_admin_password="$(openssl rand -base64 32)"
  else
    docs_admin_password="local-install-health-${target}-$(date +%s)"
  fi
  python manage.py create_docs_admin --confirm --password "$docs_admin_password"

  python scripts/check_import_resolution.py

  ./scripts/preflight-env.sh --pytest
  if [[ -f tests/test_installed_apps_manifests.py ]]; then
    python -m pytest tests/test_installed_apps_manifests.py -q
  else
    echo "Manifest test file tests/test_installed_apps_manifests.py not found; skipping manifest app validation."
  fi
  ruff check --select E9,F823 .
  lint-imports

  if [[ "$TARGET_FULL_PYTEST" == "1" ]]; then
    if python -m pytest --help | grep -q -- '--dist'; then
      xdist_args=(-n "$PYTEST_WORKERS" --dist loadfile)
    fi
    python -m pytest \
      "${TARGET_PYTEST_ARGS[@]}" \
      "${xdist_args[@]}" \
      --maxfail=1 \
      --disable-warnings \
      --durations=25 \
      -q \
      --timeout=300
  fi

  echo "==> completed $target in $(($(date +%s) - target_started_at))s"
}

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "local-install-health must run from Linux or WSL, not $(uname -s)." >&2
  exit 1
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      if [[ -z "${2:-}" ]]; then
        echo "--target requires a value." >&2
        exit 2
      fi
      TARGETS+=("$2")
      shift 2
      ;;
    --all)
      mapfile -t TARGETS < <(all_targets)
      shift
      ;;
    --list)
      all_targets
      exit 0
      ;;
    --clean)
      CLEAN=1
      shift
      ;;
    --install-system-deps)
      INSTALL_SYSTEM_DEPS=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ${#TARGETS[@]} -eq 0 ]]; then
  TARGETS=("$DEFAULT_TARGET")
fi

cd "$REPO_ROOT"
default_cache_root="${XDG_CACHE_HOME:-$HOME/.cache}/arthexis/local-install-health/$(basename "$REPO_ROOT")"
mkdir -p "${ARTHEXIS_LOCAL_INSTALL_HEALTH_ENV_ROOT:-$default_cache_root}"
CACHE_ROOT="$(cd "${ARTHEXIS_LOCAL_INSTALL_HEALTH_ENV_ROOT:-$default_cache_root}" && pwd -P)"

for target in "${TARGETS[@]}"; do
  run_target "$target"
done

echo "local install health passed for ${#TARGETS[@]} target(s)."
