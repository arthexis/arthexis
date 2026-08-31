#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
MODE="${1:-}"
INSTALL_ARGS=(--no-start)
DB_MODE="${ARTHEXIS_CI_INSTALL_SMOKE_DB_MODE:-graph}"

cd "$REPO_ROOT"

if [[ "$MODE" == "--cold" ]]; then
  rm -rf .venv
  rm -f .locks/requirements.bundle.sha256 \
        .locks/requirements.hashes \
        .locks/requirements.install-ts \
        .locks/pip.version
  INSTALL_ARGS=(--clean --no-start)
elif [[ -n "$MODE" ]]; then
  echo "Unknown option: $MODE" >&2
  exit 1
fi

case "$DB_MODE" in
  graph|apply)
    ;;
  *)
    echo "Unknown ARTHEXIS_CI_INSTALL_SMOKE_DB_MODE: $DB_MODE" >&2
    exit 1
    ;;
esac

export ARTHEXIS_DB_BACKEND="${ARTHEXIS_DB_BACKEND:-sqlite}"
export NODE_OPTIONS="${NODE_OPTIONS:---no-deprecation}"
export PYTHONDONTWRITEBYTECODE="${PYTHONDONTWRITEBYTECODE:-1}"

python "$REPO_ROOT/scripts/sort_pyproject_deps.py" --check
python "$REPO_ROOT/scripts/generate_requirements.py" --check

if [[ "$DB_MODE" == "apply" ]]; then
  ./install.sh "${INSTALL_ARGS[@]}"
else
  ./env-refresh.sh --deps-only
fi
./scripts/preflight-env.sh

source .venv/bin/activate
python scripts/check_editable_install_import.py
python manage.py check --fail-level ERROR
python scripts/check_migration_conflicts.py
python manage.py migrations check
if [[ "$DB_MODE" == "apply" ]]; then
  python manage.py migrate --noinput --database default
fi
