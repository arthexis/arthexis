#!/usr/bin/env bash
set -Eeuo pipefail

cd "$(dirname "$0")/../.."

: "${ARTHEXIS_DB_BACKEND:=sqlite}"
: "${OCPP_STATE_REDIS_URL:=redis://localhost:6379}"
export ARTHEXIS_DB_BACKEND
export OCPP_STATE_REDIS_URL

PYTEST_LOG_PATH="${PYTEST_LOG_PATH:-pytest.log}"
: > "$PYTEST_LOG_PATH"

run_step() {
  local label="$1"
  shift
  echo "==> ${label}" | tee -a "$PYTEST_LOG_PATH"
  "$@" 2>&1 | tee -a "$PYTEST_LOG_PATH"
}

run_step "Check migrations are up to date" python manage.py makemigrations --check --dry-run
run_step "Apply migrations" python manage.py migrate --noinput --database default
run_step "Check fixture import resolution" python scripts/check_import_resolution.py
run_step "Lint seed fixtures" python scripts/lint_seed_fixtures.py
run_step "Run install marker test suite" pytest --maxfail=1 --disable-warnings -q --timeout=180 -m "not critical and not slow and not integration"
run_step "Run upgrade marker test suite" pytest --maxfail=1 --disable-warnings -q --timeout=180 -m "critical"
