#!/usr/bin/env bash
set -Eeuo pipefail

expected_sha="${ARTHEXIS_EXPECTED_SHA:-}"
if [[ -z "$expected_sha" ]]; then
  echo "ARTHEXIS_EXPECTED_SHA is required" >&2
  exit 2
fi

command -v gway >/dev/null 2>&1 || {
  echo "gway is not installed on the live integration runner" >&2
  exit 2
}

if ! sudo -n true >/dev/null 2>&1; then
  echo "Passwordless sudo is required for managed live integration" >&2
  exit 2
fi

current_main_sha="$(git ls-remote https://github.com/arthexis/arthexis.git refs/heads/main | awk '{print $1}')"
if [[ "$current_main_sha" != "$expected_sha" ]]; then
  echo "Main advanced before deployment: expected $expected_sha, now $current_main_sha" >&2
  exit 1
fi

phase="managed-install"
trap 'status=$?; if [[ $status -ne 0 ]]; then echo "Live integration failed during phase: ${phase}" >&2; echo "gway=$(command -v gway)" >&2; gway --version >&2 || true; sudo -n ls -ld /opt/arthexis /opt/arthexis/app /opt/arthexis/.venv >&2 || true; fi; exit $status' EXIT

echo "=== managed install ==="
echo "GWAY runtime: $(command -v gway)"
gway --version

profile="${GWAY_SERVICE_PROFILE:-}"
if gway path arthexis >/dev/null 2>&1; then
  if [[ -z "$profile" ]]; then
    profile="$(gway arthexis node-role | tail -n 1 | tr -d '\r' | xargs)"
  fi
  echo "Existing managed Arthexis detected; refreshing it to the expected revision first"
  sudo -n gway upgrade arthexis
elif [[ -z "$profile" ]]; then
  profile="Terminal"
fi

case "$profile" in
  Control|Satellite|Terminal|Watchtower) ;;
  *)
    echo "Unable to determine canonical Arthexis service profile: $profile" >&2
    exit 1
    ;;
esac

export GWAY_SERVICE_PROFILE="$profile"
echo "Using GWAY service profile: $GWAY_SERVICE_PROFILE"

# --service makes the production install operation own service installation,
# enablement, and startup. Passing the same role to the lifecycle hook keeps
# application role and service topology in one explicit install transaction.
# The second invocation is the idempotency gate.
phase="managed-install-first"
sudo -n --preserve-env=GWAY_SERVICE_PROFILE \
  gway install arthexis --service --role "$GWAY_SERVICE_PROFILE"
phase="managed-install-second"
sudo -n --preserve-env=GWAY_SERVICE_PROFILE \
  gway install arthexis --service --role "$GWAY_SERVICE_PROFILE"

phase="managed-layout"
checkout="$(gway path arthexis)"
if [[ "$checkout" != "/opt/arthexis/app" ]]; then
  echo "Unexpected managed checkout path: $checkout" >&2
  exit 1
fi

if [[ ! -d "$checkout/.git" ]]; then
  echo "Managed Arthexis checkout is missing: $checkout" >&2
  exit 1
fi

if [[ ! -x /opt/arthexis/.venv/bin/python ]]; then
  echo "Managed Arthexis environment is missing" >&2
  exit 1
fi

if [[ ! -f /opt/arthexis/.gway/arthexis.json ]]; then
  echo "Managed Arthexis ownership metadata is missing" >&2
  exit 1
fi

phase="revision-check"
deployed_sha="$(git -C "$checkout" rev-parse HEAD)"
if [[ "$deployed_sha" != "$expected_sha" ]]; then
  echo "Managed deployment revision mismatch: expected $expected_sha, got $deployed_sha" >&2
  exit 1
fi
expected_version="$(tr -d '\r\n' < "$checkout/VERSION")"

phase="managed-command"
gway arthexis version
actual_profile="$(gway arthexis node-role | tail -n 1 | tr -d '\r' | xargs)"
if [[ "$actual_profile" != "$GWAY_SERVICE_PROFILE" ]]; then
  echo "Managed role/profile mismatch: expected $GWAY_SERVICE_PROFILE, got $actual_profile" >&2
  exit 1
fi

phase="pre-upgrade-status"
pre_upgrade_status="$(gway arthexis status --json)"
installation_id="$(STATUS_JSON="$pre_upgrade_status" python - <<'PY'
import json
import os
print(json.loads(os.environ["STATUS_JSON"])["installation_id"])
PY
)"
if [[ -z "$installation_id" || "$installation_id" == "None" ]]; then
  echo "Managed installation identity is missing before update" >&2
  exit 1
fi

phase="dirty-upgrade-refusal"
dirty_probe="$checkout/.live-integration-dirty-upgrade"
printf 'managed checkout safety probe\n' | sudo -n tee "$dirty_probe" >/dev/null
if sudo -n gway upgrade arthexis; then
  echo "Managed upgrade unexpectedly accepted a dirty checkout" >&2
  sudo -n rm -f "$dirty_probe"
  exit 1
fi
if [[ ! -f "$dirty_probe" ]]; then
  echo "Managed upgrade discarded dirty checkout state" >&2
  exit 1
fi
sudo -n rm -f "$dirty_probe"

phase="managed-upgrade-reload"
sentinel="/opt/arthexis/var/lib/.live-integration-update-preserve"
printf '%s\n' "$installation_id" | sudo -n tee "$sentinel" >/dev/null
sudo -n gway upgrade arthexis --reload

phase="managed-upgrade-noop"
sudo -n gway upgrade arthexis

phase="persistent-state-check"
if [[ "$(sudo -n cat "$sentinel")" != "$installation_id" ]]; then
  echo "Managed persistent state changed during update" >&2
  exit 1
fi
sudo -n rm -f "$sentinel"

phase="service-status"
gway service status arthexis

phase="lifecycle-status"
status_json="$(gway arthexis status --json)"
printf '%s\n' "$status_json"
STATUS_JSON="$status_json" EXPECTED_SHA="$expected_sha" EXPECTED_VERSION="$expected_version" EXPECTED_PROFILE="$GWAY_SERVICE_PROFILE" EXPECTED_INSTALLATION_ID="$installation_id" \
  python - <<'PY'
import json
import os

report = json.loads(os.environ["STATUS_JSON"])
assert report["mode"] == "managed", report
assert report["state"] == "healthy", report
assert report["installation_id"] == os.environ["EXPECTED_INSTALLATION_ID"], report
assert report["root"] == "/opt/arthexis", report
assert report["checkout"] == "/opt/arthexis/app", report
assert report["environment"] == "/opt/arthexis/.venv", report
assert report["persistent_data"] == "/opt/arthexis/var/lib", report
assert report["revision"] == os.environ["EXPECTED_SHA"], report
assert report["version"] == os.environ["EXPECTED_VERSION"], report
assert report["role"] == os.environ["EXPECTED_PROFILE"], report
assert report["dirty"] is False, report
assert report["pending_migrations"] is False, report
assert report["application_health"] == "GOOD", report
assert report["problems"] == [], report
PY

phase="application-health"
gway arthexis good

# Keep the Watchtower acceptance path focused on deployment convergence. The
# repeated install above is the idempotency gate that exercises service-aware
# reinstall behavior; destructive uninstall/reinstall coverage belongs in GWay's
# own installer/service tests and need not rebuild this persistent runner again.
phase="complete"
