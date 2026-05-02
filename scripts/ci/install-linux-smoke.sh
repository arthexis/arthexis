#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
MODE="${1:-}"
INSTALL_ARGS=(--no-start)

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

export ARTHEXIS_DB_BACKEND="${ARTHEXIS_DB_BACKEND:-sqlite}"
export NODE_OPTIONS="${NODE_OPTIONS:---no-deprecation}"
export PYTHONDONTWRITEBYTECODE="${PYTHONDONTWRITEBYTECODE:-1}"

python "$REPO_ROOT/scripts/sort_pyproject_deps.py" --check
python "$REPO_ROOT/scripts/generate_requirements.py" --check

./install.sh "${INSTALL_ARGS[@]}"
./scripts/preflight-env.sh

source .venv/bin/activate
python scripts/check_editable_install_import.py
python scripts/check_migration_conflicts.py
python manage.py migrations check
python manage.py migrate --noinput --database default
