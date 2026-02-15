#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT_DIR"

PYTEST_LOG="${PYTEST_LOG:-$ROOT_DIR/pytest.log}"
: > "$PYTEST_LOG"

run_pytest() {
  local marker_expr="$1"
  echo "Running pytest with marker expression: ${marker_expr}" | tee -a "$PYTEST_LOG"
  pytest --maxfail=1 --disable-warnings -q --timeout=180 -m "$marker_expr" 2>&1 | tee -a "$PYTEST_LOG"
}

echo "[CI] Checking for model changes requiring migrations"
python manage.py makemigrations --check --dry-run

echo "[CI] Applying migrations"
python manage.py migrate --noinput --database default

echo "[CI] Checking fixture import resolution"
python scripts/check_import_resolution.py

echo "[CI] Linting seed fixtures"
python scripts/lint_seed_fixtures.py

run_pytest "not critical and not slow and not integration"
run_pytest "critical"
