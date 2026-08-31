#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$BASE_DIR/.venv/bin/python}"

: "${IMAGER_CONNECT_BASE_IMAGE_URI:?Set IMAGER_CONNECT_BASE_IMAGE_URI to the Raspberry Pi OS base image URI.}"
: "${IMAGER_CONNECT_DOWNLOAD_BASE_URI:?Set IMAGER_CONNECT_DOWNLOAD_BASE_URI to the public artifact base URL.}"

append_feature_pack() {
  local pack="$1"
  local current="${ARTHEXIS_ROLE_APP_FEATURE_PACKS:-${ARTHEXIS_FEATURE_PACKS:-}}"
  local normalized_current="${current//,/ }"
  normalized_current="${normalized_current//;/ }"
  case " $normalized_current " in
    *" $pack "*) printf '%s\n' "$current" ;;
    *) printf '%s\n' "${current:+$current,}$pack" ;;
  esac
}

export ARTHEXIS_ROLE_APP_FEATURE_PACKS="$(append_feature_pack rpi_connect_updates)"

release_version="${IMAGER_CONNECT_RELEASE_VERSION:-$(date -u +%Y.%m.%d)}"
artifact_name="${IMAGER_CONNECT_ARTIFACT_NAME:-connect-universal-${release_version}}"
output_dir="${IMAGER_CONNECT_OUTPUT_DIR:-build/rpi-connect-updates}"
ota_channel="${IMAGER_CONNECT_CHANNEL:-stable}"
retention_days="${IMAGER_CONNECT_RETENTION_DAYS:-30}"

profile_metadata="$("$PYTHON_BIN" - "$release_version" "$ota_channel" <<'PY'
import json
import sys

release_version, ota_channel = sys.argv[1:3]
print(
    json.dumps(
        {
            "base_os": "raspberry-pi-os-trixie",
            "architecture": "arm64",
            "release_version": release_version,
            "compatibility_model": "raspberry-pi",
            "compatibility_board": "rpi-4b",
            "ota_channel": ota_channel,
            "ota_artifact_type": "raw-disk-image",
            "required_artifacts": [
                "connect-ota-agent",
                "connect-ota-channel-config",
                "connect-ota-device-identity",
            ],
            "universal_update": True,
            "supported_roles": ["Terminal", "Satellite", "Control", "Watchtower"],
            "device_configuration_policy": "preserve-local-role-locks-enabled-app-locks-hardware-choices",
        },
        sort_keys=True,
    )
)
PY
)"

cd "$BASE_DIR"

if ! "$PYTHON_BIN" manage.py shell -c '
from django.apps import apps

missing = [
    selector
    for selector in ("apps.imager", "apps.rpiconnect")
    if not apps.is_installed(selector)
]
if missing:
    raise SystemExit("Missing required installed app(s): " + ", ".join(missing))
'; then
  cat >&2 <<'EOF'
Enable the native Raspberry Connect update feature pack on the authorized Watchtower, restart the suite, then rerun this script:

  .venv/bin/python manage.py enabled_apps_lock --role Watchtower --feature-pack rpi_connect_updates --write

No container runtime is required for this artifact job.
EOF
  exit 78
fi

"$PYTHON_BIN" manage.py imager build \
  --name "$artifact_name" \
  --base-image-uri "$IMAGER_CONNECT_BASE_IMAGE_URI" \
  --output-dir "$output_dir" \
  --download-base-uri "$IMAGER_CONNECT_DOWNLOAD_BASE_URI" \
  --profile connect-ota \
  --profile-metadata "$profile_metadata" \
  --skip-recovery-ssh \
  --no-copy-parent-network \
  --no-reserve

"$PYTHON_BIN" manage.py imager register-connect-release \
  --artifact "$artifact_name" \
  --version "$release_version" \
  --retention-days "$retention_days"
