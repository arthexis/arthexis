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

if gway path arthexis >/dev/null 2>&1; then
  sudo -n gway upgrade arthexis
else
  sudo -n gway install arthexis
fi

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

deployed_sha="$(git -C "$checkout" rev-parse HEAD)"
if [[ "$deployed_sha" != "$expected_sha" ]]; then
  echo "Managed deployment revision mismatch: expected $expected_sha, got $deployed_sha" >&2
  exit 1
fi

gway arthexis version
profile="${GWAY_SERVICE_PROFILE:-}"
if [[ -z "$profile" ]]; then
  profile="$(gway arthexis node-role | tail -n 1 | tr -d '\r' | xargs)"
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

sudo -n --preserve-env=GWAY_SERVICE_PROFILE gway service install arthexis
sudo -n --preserve-env=GWAY_SERVICE_PROFILE gway service start arthexis
gway service status arthexis

gway arthexis status
gway arthexis good
