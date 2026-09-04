#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$REPO_ROOT"

export ARTHEXIS_DB_BACKEND="${ARTHEXIS_DB_BACKEND:-sqlite}"
export NODE_OPTIONS="${NODE_OPTIONS:---no-deprecation}"
export OCPP_STATE_REDIS_URL="${OCPP_STATE_REDIS_URL:-redis://localhost:6379}"
export PYTHONDONTWRITEBYTECODE="${PYTHONDONTWRITEBYTECODE:-1}"

if [[ "${ARTHEXIS_SKIP_SANITY_APT:-0}" != "1" ]] && command -v apt-get >/dev/null 2>&1; then
  sudo_cmd=()
  if [[ "$(id -u)" != "0" ]]; then
    if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
      sudo_cmd=(sudo -n)
    else
      echo "Passwordless sudo is required to install native sanity-check dependencies." >&2
      echo "Install them manually or set ARTHEXIS_SKIP_SANITY_APT=1 after dependencies are present." >&2
      exit 1
    fi
  fi
  "${sudo_cmd[@]}" apt-get update
  "${sudo_cmd[@]}" apt-get install -y --no-install-recommends \
    ca-certificates curl git python3-venv \
    libcairo2 libgdk-pixbuf-2.0-0 libpango-1.0-0 \
    libpangocairo-1.0-0 shared-mime-info
fi

./scripts/ci/start-native-redis.sh

if [[ "${ARTHEXIS_FORCE_SANITY_INSTALL:-0}" == "1" ]]; then
  ./scripts/ci/install-linux-smoke.sh --cold
else
  ./scripts/ci/install-linux-smoke.sh
fi

source .venv/bin/activate
python -m pip install --only-binary=:all: -r requirements-ci.txt

# Verify the installed environment is internally consistent after the smoke install.
python -m pip check

python scripts/sort_pyproject_deps.py --check
python scripts/generate_requirements.py --check
python scripts/check_import_resolution.py
python -m ruff check --select E9,F821,F823 .

# Import and collect the complete test corpus without paying the cost of executing it.
# This catches broken modules, fixtures, plugins, and collection-time Django failures.
python -m pytest --collect-only -q

# Exercise the small, documented critical-path matrix alongside CI policy regressions.
python -m pytest \
  apps/nodes/tests/test_enrollment.py::test_submit_enrollment_public_key_rejects_duplicate_submission_regression \
  apps/ocpp/tests/test_charger_status_polling.py::test_dedupe_event_rows_keeps_newest_status_for_out_of_order_retry_collisions \
  tests/test_nodes_registration.py::test_register_visitor_proxy_reports_partial_failure_on_visitor_confirmation \
  apps/sites/tests/test_public_routes.py::test_require_site_operator_or_staff_enforces_admin_operator_boundary \
  apps/core/tests/reports/test_release_publish_regressions.py::test_github_workflows_do_not_define_windows_gates \
  apps/core/tests/reports/test_release_publish_regressions.py::test_linux_ci_and_security_scans_run_on_pull_requests \
  apps/core/tests/reports/test_release_publish_regressions.py::test_security_workflows_keep_scheduled_baseline_scans \
  apps/core/tests/reports/test_release_publish_regressions.py::test_linux_ci_uses_single_sanity_job \
  -q

# Build the artifact users receive; editable-install checks alone can miss packaging errors.
wheel_dir="$(mktemp -d)"
trap 'rm -rf "$wheel_dir"' EXIT
python -m pip wheel --no-deps --wheel-dir "$wheel_dir" .
python -m twine check "$wheel_dir"/*.whl
