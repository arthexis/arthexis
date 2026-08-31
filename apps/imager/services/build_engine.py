"""Imager build orchestration and operator-facing service entrypoints."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import tarfile
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath, PureWindowsPath
from tempfile import NamedTemporaryFile, TemporaryDirectory
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback.
    import tomli as tomllib  # type: ignore[no-redef]

if os.name == "nt":
    msvcrt = importlib.import_module("msvcrt")
    fcntl = None
else:
    fcntl = importlib.import_module("fcntl")
    msvcrt = None

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.serialization import load_ssh_public_key
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.imager.models import RaspberryPiImageArtifact
from apps.imager.reservations import (
    RESERVATION_ENV_PATH,
    RESERVATION_JSON_PATH,
    ImageReservation,
    RemoteReservationError,
    active_parent_network_names,
    commit_image_reservation,
    plan_image_reservation,
    render_reservation_env,
    render_reservation_json,
)

from .artifacts import (
    LOCAL_HTTP_SCHEME,
    _build_download_uri,
    _build_local_http_url,
    _build_profile_manifest,
    _build_served_artifact_url,
    _coerce_profile_metadata,
    _format_url_host,
    _image_size_metadata,
    _network_profiles_metadata,
    _reservation_metadata,
    _sanitize_storage_options,
    _sha256_for_file,
    _sha256_for_prefix,
    _suite_bundle_metadata,
)
from .guestfish import (
    _ensure_guestfish,
    _ensure_image_minimum_size,
    _guestfish_mkdir_p_command,
    _guestfish_remove_file_command,
    _guestfish_run_commands,
    _guestfish_symlink_command,
    _guestfish_upload_commands,
    _normalize_minimum_image_size_bytes,
    _run_guestfish_raw_script,
)
from .models import (
    DEFAULT_IMAGE_WRITE_BACKUP_DIR,
    DEFAULT_IMAGE_WRITE_CHUNK_SIZE_BYTES,
    DEFAULT_IMAGE_WRITE_MIN_RATE_BYTES_PER_SECOND,
    DEFAULT_IMAGE_WRITE_SPEED_GRACE_SECONDS,
    DEFAULT_RECOVERY_SSH_USER,
    NETWORK_MANAGER_CONNECTIONS_REMOTE_PATH,
    RECOVERY_SSH_FORBIDDEN_USERS,
    RECOVERY_SSH_USERNAME_PATTERN,
    STORAGE_BACKEND_AZURE_BLOB,
    STORAGE_BACKEND_GCS,
    STORAGE_BACKEND_LOCAL,
    STORAGE_BACKEND_S3,
    SUITE_BUNDLE_EXCLUDED_NAMES,
    SUITE_BUNDLE_EXCLUDED_TOP_LEVEL,
    SUITE_BUNDLE_REMOTE_PATH,
    SUPPORTED_STORAGE_BACKENDS,
    TARGET_RPI4B,
    VALID_PUBLIC_KEY_PATTERN,
    AccessCheckResult,
    BlockDeviceInfo,
    BuildEngineProfile,
    BuildResult,
    ImageCustomizationResult,
    ImagerBuildError,
    ImageSizeAdjustment,
    NetworkProfileInfo,
    RecoveryAuthorizedKeyError,
    RecoverySSHAccess,
    RpiAccessTestResult,
    ServeResult,
    SuiteBundleInfo,
    WriteBackupResult,
    WriteResult,
)
from .network_profiles import select_host_network_profiles
from .source import _download_remote_base_image, _resolve_base_image


def _validate_initial_profile(profile_path: Path) -> None:
    """Reject malformed first-boot profiles before an image is produced."""

    _load_initial_profile_build_settings(profile_path)


def _connect_auth_key_from_file(auth_key_path: Path) -> str:
    """Read a Connect auth key from a mode-0600 raw or TOML source file."""

    try:
        mode = auth_key_path.stat().st_mode
    except FileNotFoundError as exc:
        raise ImagerBuildError(
            f"Raspberry Pi Connect auth key file does not exist: {auth_key_path}"
        ) from exc
    if mode & 0o077:
        raise ImagerBuildError(
            f"Raspberry Pi Connect auth key file must be mode 0600: {auth_key_path}"
        )
    try:
        content = auth_key_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ImagerBuildError(
            f"Could not read Raspberry Pi Connect auth key file: {exc}"
        ) from exc
    try:
        data = tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        auth_key = content.strip()
    else:
        section = data.get("rpi_connect")
        if not isinstance(section, dict):
            section = data.get("connect")
        auth_key = (
            str(section.get("auth_key", "")).strip()
            if isinstance(section, dict)
            else ""
        )
    if not auth_key:
        raise ImagerBuildError(
            f"Raspberry Pi Connect auth key file is empty: {auth_key_path}"
        )
    return auth_key


def _write_connect_auth_key_for_injection(source_path: Path, work_dir: Path) -> Path:
    """Materialize only the auth key for image injection, excluding TOML comments."""

    auth_key = _connect_auth_key_from_file(source_path)
    destination = work_dir / "rpi-connect-auth.key"
    descriptor = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as destination_file:
        # codeql[py/clear-text-storage-sensitive-data] One-time mode-0600 image injection; bootstrap securely erases it.
        destination_file.write(auth_key + "\n")
    return destination


def _load_initial_profile_build_settings(profile_path: Path):
    """Return validated image-build settings declared in a private profile."""

    from apps.cards.initial_profile import InitialProfileError
    from apps.imager.initial_profile import load_initial_profile

    try:
        return load_initial_profile(profile_path)
    except InitialProfileError as exc:
        raise ImagerBuildError(str(exc)) from exc


BOOTSTRAP_SCRIPT = """#!/usr/bin/env bash
set -euo pipefail

RESERVED_NODE_ENV=/usr/local/share/arthexis/reserved-node.env
if [ -f "$RESERVED_NODE_ENV" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$RESERVED_NODE_ENV"
  set +a
fi

if [ -n "${NODE_HOSTNAME:-}" ]; then
  hostnamectl set-hostname "$NODE_HOSTNAME" 2>/dev/null || hostname "$NODE_HOSTNAME" 2>/dev/null || true
  if ! awk -v host="$NODE_HOSTNAME" '$1 !~ /^#/ { for (i = 2; i <= NF; i++) if ($i == host) found = 1 } END { exit found ? 0 : 1 }' /etc/hosts 2>/dev/null; then
    printf '127.0.1.1\\t%s\\n' "$NODE_HOSTNAME" >> /etc/hosts
  fi
fi

ARTHEXIS_BUNDLE=/usr/local/share/arthexis/arthexis-suite.tar.gz
INITIAL_PROFILE=/usr/local/share/arthexis/initial-profile.toml
CONNECT_AUTH_KEY=/usr/local/share/arthexis/rpi-connect-auth.key
BOOTSTRAP_COMPLETE=/var/lib/arthexis/bootstrap-complete

cleanup_connect_auth_key() {
  [ -e "$CONNECT_AUTH_KEY" ] || return 0
  shred -u "$CONNECT_AUTH_KEY" >/dev/null 2>&1 || rm -f "$CONNECT_AUTH_KEY"
}

disable_bootstrap_service() {
  systemctl disable arthexis-bootstrap.service >/dev/null 2>&1 || true
  rm -f /etc/systemd/system/multi-user.target.wants/arthexis-bootstrap.service || true
}

if [ -f "$BOOTSTRAP_COMPLETE" ]; then
  cleanup_connect_auth_key
  disable_bootstrap_service
  exit 0
fi

add_required_package_if_missing() {
  local package="$1"
  if ! dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q "ok installed"; then
    required_packages+=("$package")
  fi
}

bootstrap_app_user() {
  local candidate
  for candidate in "${ARTHEXIS_BOOTSTRAP_USER:-}" arthe "${SUDO_USER:-}"; do
    [ -n "$candidate" ] || continue
    if id "$candidate" >/dev/null 2>&1; then
      printf '%s\\n' "$candidate"
      return 0
    fi
  done
}

remove_app_env_value() {
  local key="$1"
  local value="$2"
  local env_file="$APP_HOME/arthexis.env"
  local tmp_file
  [ -f "$env_file" ] || return 0
  tmp_file="$(mktemp)"
  awk -v stale_assignment="${key}=${value}" '$0 != stale_assignment { print }' "$env_file" > "$tmp_file"
  mv "$tmp_file" "$env_file"
  chmod 600 "$env_file" || true
}

set_app_env_default() {
  local key="$1"
  local value="$2"
  local env_file="$APP_HOME/arthexis.env"
  touch "$env_file"
  chmod 600 "$env_file" || true
  grep -q "^${key}=" "$env_file" 2>/dev/null || \\
    printf '%s=%s\\n' "$key" "$value" >> "$env_file"
}

set_app_env_value() {
  local key="$1"
  local value="$2"
  local env_file="$APP_HOME/arthexis.env"
  local tmp_file
  tmp_file="$(mktemp)"
  if [ -f "$env_file" ]; then
    awk -v prefix="${key}=" 'index($0, prefix) != 1 { print }' "$env_file" > "$tmp_file"
  fi
  printf '%s=%s\n' "$key" "$value" >> "$tmp_file"
  mv "$tmp_file" "$env_file"
  chmod 600 "$env_file" || true
}

bootstrap_role_env_defaults() {
  local role="${NODE_ROLE:-Terminal}"
  case "${role,,}" in
    satellite|control)
      set_app_env_default OCPP_AUTHORIZATION_POLICY open
      ;;
  esac
}

bootstrap_normalize_runtime_role() {
  local role="${NODE_ROLE:-Terminal}"
  case "${role,,}" in
    satellite) NODE_ROLE=Satellite ;;
    control) NODE_ROLE=Control ;;
    watchtower|constellation) NODE_ROLE=Watchtower ;;
    terminal|'') NODE_ROLE=Terminal ;;
    *)
      echo "Unknown NODE_ROLE '$role'; falling back to Terminal bootstrap" >&2
      NODE_ROLE=Terminal
      ;;
  esac
  export NODE_ROLE
}

bootstrap_persist_runtime_role() {
  set_app_env_value NODE_ROLE "$NODE_ROLE"
}

bootstrap_install_args() {
  local role="${NODE_ROLE:-Terminal}"
  # Start the service only after bootstrap finishes its final migrations and
  # optional initial-profile application.  Starting during install lets the
  # systemd migration preflight race this script against the fresh database.
  local start_arg=--no-start
  case "${role,,}" in
    satellite)
      printf '%s\\n' --satellite --no-rfid-service --systemd "$start_arg" --repair
      ;;
    control)
      printf '%s\\n' --control --systemd "$start_arg" --repair
      ;;
    watchtower)
      printf '%s\\n' --watchtower --systemd "$start_arg" --repair
      ;;
    terminal|'')
      printf '%s\\n' --terminal --no-celery --systemd "$start_arg" --repair
      ;;
    *)
      echo "Unknown NODE_ROLE '$role'; falling back to Terminal bootstrap" >&2
      printf '%s\\n' --terminal --no-celery --systemd "$start_arg" --repair
      ;;
  esac
}

bootstrap_start_app() {
  # install.sh normally re-execs as the owner of APP_HOME before it invokes
  # start.sh.  Bootstrap defers that invocation until runtime migrations are
  # complete, so preserve the same ownership boundary for lifecycle logs and
  # lock files created by start.sh.
  if [ -n "$APP_USER" ]; then
    sudo -u "$APP_USER" ./start.sh
  else
    ./start.sh
  fi
}

bootstrap_select_recovery_ap_iface() {
  if [ -n "${ARTHEXIS_RECOVERY_AP_IFACE:-}" ]; then
    printf '%s\\n' "$ARTHEXIS_RECOVERY_AP_IFACE"
    return 0
  fi
  nmcli -t -f DEVICE,TYPE device status 2>/dev/null | awk -F: '$2 == "wifi" && $1 !~ /^p2p-/ { print $1; exit }'
}

bootstrap_validate_recovery_ap_psk() {
  local psk="$1"
  [ "${#psk}" -ge 8 ] && [ "${#psk}" -le 63 ] || return 1
  LC_ALL=C grep -qx '[ -~]\\{8,63\\}' <<<"$psk"
}

bootstrap_recovery_ap_psk() {
  if [ -n "${ARTHEXIS_RECOVERY_AP_PSK:-}" ]; then
    bootstrap_validate_recovery_ap_psk "$ARTHEXIS_RECOVERY_AP_PSK" || return 1
    printf '%s\\n' "$ARTHEXIS_RECOVERY_AP_PSK"
    return 0
  fi
  local psk_file="${ARTHEXIS_RECOVERY_AP_PSK_FILE:-/etc/arthexis/recovery-ap.psk}"
  if [ -s "$psk_file" ]; then
    local file_psk
    file_psk="$(tr -d '\r\n' < "$psk_file")"
    bootstrap_validate_recovery_ap_psk "$file_psk" || return 1
    printf '%s\\n' "$file_psk"
    return 0
  fi
  local generated_psk
  if command -v openssl >/dev/null 2>&1; then
    generated_psk="$(openssl rand -base64 24 | tr -d '\r\n')"
  else
    generated_psk="$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32 || true)"
  fi
  bootstrap_validate_recovery_ap_psk "$generated_psk" || return 1
  install -d -m 700 "$(dirname "$psk_file")" >/dev/null 2>&1 || return 1
  ( umask 077 && printf '%s\\n' "$generated_psk" > "$psk_file" ) || return 1
  printf '%s\\n' "$generated_psk"
}

bootstrap_enable_recovery_ap() {
  command -v nmcli >/dev/null 2>&1 || return 0
  local role="${NODE_ROLE:-Terminal}"
  case "${role,,}" in
    satellite|control)
      ;;
    *)
      return 0
      ;;
  esac
  local ap_iface
  ap_iface="$(bootstrap_select_recovery_ap_iface)"
  [ -n "$ap_iface" ] || return 0
  local host="${NODE_HOSTNAME:-${NODE_RESERVED_HOSTNAME:-}}"
  local number="${host##*-}"
  case "$number" in
    ''|*[!0-9]*) return 0 ;;
  esac
  local short_number="$((10#$number))"
  local ap_ssid="${ARTHEXIS_RECOVERY_AP_SSID:-arthexis-${short_number}}"
  local ap_psk
  ap_psk="$(bootstrap_recovery_ap_psk)" || return 0
  [ -n "$ap_psk" ] || return 0
  local ap_channel="${ARTHEXIS_RECOVERY_AP_CHANNEL:-1}"
  nmcli radio wifi on >/dev/null 2>&1 || true
  nmcli con delete "$ap_ssid" >/dev/null 2>&1 || true
  nmcli con add type wifi ifname "$ap_iface" con-name "$ap_ssid" autoconnect yes ssid "$ap_ssid" >/dev/null 2>&1 || return 0
  if ! nmcli con mod "$ap_ssid" connection.interface-name "$ap_iface" connection.autoconnect yes connection.autoconnect-priority 100 802-11-wireless.mode ap 802-11-wireless.hidden yes 802-11-wireless.band bg 802-11-wireless.channel "$ap_channel" ipv4.method shared ipv4.addresses 10.42.0.1/16 wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$ap_psk" >/dev/null 2>&1; then
    nmcli con delete "$ap_ssid" >/dev/null 2>&1 || true
    return 0
  fi
  nmcli con up "$ap_ssid" >/dev/null 2>&1 || true
}

wait_for_bootstrap_clock_sync() {
  timedatectl set-ntp true >/dev/null 2>&1 || true
  systemctl restart systemd-timesyncd.service >/dev/null 2>&1 || true
  if command -v timedatectl >/dev/null 2>&1; then
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
      if timedatectl show -p NTPSynchronized 2>/dev/null | grep -q "=yes"; then
        return 0
      fi
      sleep 5
    done
  else
    sleep 60
  fi
}

apt_get_update_with_clock_retry() {
  local attempt max_attempts output status
  max_attempts="${ARTHEXIS_BOOTSTRAP_APT_UPDATE_ATTEMPTS:-12}"
  case "$max_attempts" in
    ''|*[!0-9]*) max_attempts=12 ;;
  esac
  attempt=1
  status=1
  while [ "$attempt" -le "$max_attempts" ]; do
    if output="$(apt-get update 2>&1)"; then
      printf '%s\\n' "$output"
      return 0
    else
      status=$?
    fi
    printf '%s\\n' "$output" >&2
    if ! grep -qi "not valid yet" <<<"$output"; then
      return "$status"
    fi
    echo "apt release metadata is not valid yet; waiting for clock synchronization before retrying ($attempt/$max_attempts)" >&2
    wait_for_bootstrap_clock_sync || true
    attempt=$((attempt + 1))
  done
  return "$status"
}

bootstrap_normalize_runtime_role

required_packages=()
for package in python3-venv python3-dev build-essential libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 shared-mime-info fonts-dejavu-core; do
  add_required_package_if_missing "$package"
done
case "${NODE_ROLE:-Terminal}" in
  [Ss]atellite|[Cc]ontrol|[Ww]atchtower)
    add_required_package_if_missing redis-server
    ;;
esac
if ! command -v git >/dev/null 2>&1 && [ ! -f "$ARTHEXIS_BUNDLE" ]; then
  required_packages+=(git ca-certificates)
elif [ ! -e /etc/ssl/certs/ca-certificates.crt ]; then
  required_packages+=(ca-certificates)
fi
if [ -n "${ARTHEXIS_DOWNSTREAM_REGISTRATION_BASE_URL:-}" ]; then
  add_required_package_if_missing curl
fi
if [ "${ARTHEXIS_INITIAL_PROFILE_REQUIRES_NFTABLES:-0}" = "1" ]; then
  add_required_package_if_missing nftables
fi
optional_connect_packages=()
connect_bootstrap_enabled="${ARTHEXIS_ENABLE_CONNECT_BOOTSTRAP:-0}"
if [ "$connect_bootstrap_enabled" = "1" ]; then
  for package in rpi-connect wayvnc wfplug-connect lightdm pi-greeter wayfire labwc; do
    if ! dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q "ok installed"; then
      optional_connect_packages+=("$package")
    fi
  done
fi

if [ "${#required_packages[@]}" -gt 0 ]; then
  export DEBIAN_FRONTEND=noninteractive
  apt_get_update_with_clock_retry
  apt-get install -y --no-install-recommends "${required_packages[@]}"
fi

if [ "${#optional_connect_packages[@]}" -gt 0 ]; then
  export DEBIAN_FRONTEND=noninteractive
  if apt_get_update_with_clock_retry; then
    for package in "${optional_connect_packages[@]}"; do
      apt-get install -y --no-install-recommends "$package" || echo "Optional Raspberry Pi Connect package '$package' failed to install; continuing bootstrap" >&2
    done
  else
    echo "Optional Raspberry Pi Connect package index update failed; continuing bootstrap" >&2
  fi
fi

CONNECT_SCREEN_SHARE_USER="${ARTHEXIS_CONNECT_USER:-arthe}"
if [ "$connect_bootstrap_enabled" = "1" ] && id "$CONNECT_SCREEN_SHARE_USER" >/dev/null 2>&1; then
  systemctl stop userconfig.service >/dev/null 2>&1 || true
  systemctl disable userconfig.service >/dev/null 2>&1 || true
  loginctl enable-linger "$CONNECT_SCREEN_SHARE_USER" >/dev/null 2>&1 || true

  if [ -d /etc/lightdm ]; then
    install -d -m 755 /etc/lightdm/lightdm.conf.d
    cat >/etc/lightdm/lightdm.conf.d/20-arthexis-connect.conf <<EOF
[Seat:*]
greeter-session=pi-greeter-labwc
user-session=rpd-labwc
autologin-user=$CONNECT_SCREEN_SHARE_USER
autologin-session=rpd-labwc
EOF
    systemctl set-default graphical.target >/dev/null 2>&1 || true
    systemctl enable lightdm.service >/dev/null 2>&1 || true
    systemctl start --no-block lightdm.service >/dev/null 2>&1 || true
  fi

  if command -v rpi-connect >/dev/null 2>&1; then
    connect_uid="$(id -u "$CONNECT_SCREEN_SHARE_USER")"
    systemctl enable "user@${connect_uid}.service" >/dev/null 2>&1 || true
    systemctl start --no-block "user@${connect_uid}.service" >/dev/null 2>&1 || true
    connect_env=(XDG_RUNTIME_DIR="/run/user/${connect_uid}" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${connect_uid}/bus")
    sudo -u "$CONNECT_SCREEN_SHARE_USER" env "${connect_env[@]}" rpi-connect on >/dev/null 2>&1 || true
    if [ -s "$CONNECT_AUTH_KEY" ]; then
      auth_key="$(cat "$CONNECT_AUTH_KEY")"
      signin_rc=1
      for signin_attempt in 1 2 3; do
        set +e
        sudo -u "$CONNECT_SCREEN_SHARE_USER" env "${connect_env[@]}" rpi-connect signin -auth-key "$auth_key" >/dev/null 2>&1
        signin_rc=$?
        set -e
        [ "$signin_rc" -eq 0 ] && break
        if [ "$signin_attempt" -lt 3 ]; then
          echo "Raspberry Pi Connect auth key sign-in failed; retrying" >&2
          sleep 5
        fi
      done
      unset auth_key
      cleanup_connect_auth_key
      if [ "$signin_rc" -ne 0 ]; then
        echo "Raspberry Pi Connect auth key sign-in failed after retries; continuing bootstrap" >&2
      fi
    fi
    timeout 30s sudo -u "$CONNECT_SCREEN_SHARE_USER" env "${connect_env[@]}" rpi-connect shell on >/dev/null 2>&1 || true
    timeout 30s sudo -u "$CONNECT_SCREEN_SHARE_USER" env "${connect_env[@]}" rpi-connect vnc on >/dev/null 2>&1 || true
  fi
fi

APP_HOME=/opt/arthexis
if [ ! -x "$APP_HOME/start.sh" ] && [ -f "$ARTHEXIS_BUNDLE" ]; then
  rm -rf "$APP_HOME"
  install -d -m 755 "$APP_HOME"
  tar -xzf "$ARTHEXIS_BUNDLE" -C "$APP_HOME"
  chmod +x "$APP_HOME"/install.sh "$APP_HOME"/env-refresh.sh "$APP_HOME"/start.sh "$APP_HOME"/manage.py "$APP_HOME"/command.sh 2>/dev/null || true
fi

if [ ! -x "$APP_HOME/start.sh" ]; then
  if [ -z "${ARTHEXIS_GIT_URL:-}" ]; then
    echo "No bundled Arthexis suite was available and ARTHEXIS_GIT_URL is not configured." >&2
    echo "Rebuild the image with the default suite bundle or pass an authenticated --git-url." >&2
    exit 1
  fi
  git clone --depth 1 "${ARTHEXIS_GIT_URL}" "$APP_HOME"
fi

if [ -f "$RESERVED_NODE_ENV" ]; then
  touch "$APP_HOME/arthexis.env"
  chmod 600 "$APP_HOME/arthexis.env" || true
  while IFS= read -r line; do
    case "$line" in
      NODE_*=*)
        key="${line%%=*}"
        if ! grep -q "^${key}=" "$APP_HOME/arthexis.env"; then
          printf '%s\\n' "$line" >> "$APP_HOME/arthexis.env"
        fi
        ;;
    esac
  done < "$RESERVED_NODE_ENV"
fi
remove_app_env_value ARTHEXIS_RUNSERVER_HOST 0.0.0.0
bootstrap_persist_runtime_role
bootstrap_role_env_defaults

register_downstream_with_arthexis() {
  local upstream_base="${ARTHEXIS_DOWNSTREAM_REGISTRATION_BASE_URL:-}"
  [ -n "$upstream_base" ] || return 0
  local local_base="${ARTHEXIS_LOCAL_REGISTRATION_BASE_URL:-http://localhost:${ARTHEXIS_RUNSERVER_PORT:-8888}}"
  local attempts="${ARTHEXIS_DOWNSTREAM_REGISTRATION_ATTEMPTS:-12}"
  local attempt script
  case "$attempts" in
    ''|*[!0-9]*) attempts=12 ;;
  esac
  attempt=1
  while [ "$attempt" -le "$attempts" ]; do
    script="$(mktemp)"
    if /usr/local/bin/arthexis node register-curl "$upstream_base" --local-base "$local_base" > "$script" && bash "$script"; then
      rm -f "$script"
      return 0
    fi
    rm -f "$script"
    echo "Downstream registration with $upstream_base failed; retrying ($attempt/$attempts)" >&2
    sleep 10
    attempt=$((attempt + 1))
  done
  echo "Downstream registration with $upstream_base failed after $attempts attempts" >&2
  return 1
}

APP_USER="$(bootstrap_app_user || true)"
if [ -n "$APP_USER" ]; then
  APP_GROUP="$(id -gn "$APP_USER")"
  chown -R "$APP_USER:$APP_GROUP" "$APP_HOME"
fi

cd "$APP_HOME"
chmod +x ./install.sh ./env-refresh.sh ./start.sh ./manage.py ./command.sh 2>/dev/null || true
bootstrap_enable_recovery_ap
cat >/usr/local/bin/arthexis <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

APP_HOME=/opt/arthexis
if [ ! -x "$APP_HOME/command.sh" ]; then
  echo "Arthexis command entrypoint not found at $APP_HOME/command.sh" >&2
  exit 1
fi

export ARTHEXIS_CALLER_CWD="$(pwd -P)"
exec "$APP_HOME/command.sh" "$@"
EOF
chmod +x /usr/local/bin/arthexis
wait_for_bootstrap_clock_sync || true
mapfile -t install_args < <(bootstrap_install_args)
# A bootstrap image always starts with its own fresh database. Satellite nodes
# normally default to check-only migrations, but that leaves a first-boot image
# unable to start the suite when the bundled app set has unapplied migrations.
ARTHEXIS_MIGRATION_POLICY=apply ./install.sh "${install_args[@]}"
# ``install.sh`` can migrate a narrower app selection while preparing a role.
# Verify and reconcile against the exact settings the service will use before
# switching Satellite and Watchtower nodes back to their check-only policy.
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py migrate --check
# Keep the one-time bootstrap override from changing later upgrade behavior.
bootstrap_runtime_role="${NODE_ROLE:-Terminal}"
case "${bootstrap_runtime_role,,}" in
  satellite|watchtower)
    printf 'ARTHEXIS_MIGRATION_POLICY=check\n' > "$APP_HOME/migration.env"
    ;;
esac
if [ -f "$INITIAL_PROFILE" ]; then
  .venv/bin/python manage.py imager initial-profile --apply --profile "$INITIAL_PROFILE"
fi
bootstrap_start_app
bootstrap_enable_recovery_ap
register_downstream_with_arthexis || echo "Downstream registration failed; continuing bootstrap" >&2
cleanup_connect_auth_key
install -d -m 755 "$(dirname "$BOOTSTRAP_COMPLETE")"
touch "$BOOTSTRAP_COMPLETE"
disable_bootstrap_service
"""

RECOVERY_AUTHORIZED_KEYS_REMOTE_PATH = (
    "/usr/local/share/arthexis/recovery_authorized_keys"
)
RECOVERY_AP_PSK_REMOTE_PATH = "/etc/arthexis/recovery-ap.psk"
INITIAL_PROFILE_REMOTE_PATH = "/usr/local/share/arthexis/initial-profile.toml"
CONNECT_AUTH_KEY_REMOTE_PATH = "/usr/local/share/arthexis/rpi-connect-auth.key"
RECOVERY_SSHD_CONFIG_REMOTE_PATH = "/etc/ssh/sshd_config.d/20-arthexis-recovery.conf"
RPI_BOOT_PARTITION_DEVICE = "/dev/sda1"
RECOVERY_BOOT_USERCONF_PATH = "/userconf.txt"
RECOVERY_BOOT_SSH_MARKER_PATH = "/ssh"
BOOTSTRAP_SYSTEMD_SERVICE_PATH = "/etc/systemd/system/arthexis-bootstrap.service"
RECOVERY_SYSTEMD_SERVICE_PATH = "/etc/systemd/system/arthexis-recovery-access.service"
SYSTEMD_MULTI_USER_WANTS_PATH = "/etc/systemd/system/multi-user.target.wants"
_BLOCKED_WRITE_MEDIA = (
    (
        "lacie",
        "iamakey",
        "LaCie iamaKey media is reserved for bastion USB unlock keys.",
    ),
)

_WINDOWS_VOLUME_GUID_ACCESS_PATH_RE = re.compile(
    r"^\\\\\?\\Volume\{[0-9A-Fa-f-]+\}\\?$",
    re.IGNORECASE,
)
_WINDOWS_PHYSICAL_DRIVE_PATH_RE = re.compile(
    r"^\\\\\.\\PhysicalDrive\d+$",
    re.IGNORECASE,
)
_WINDOWS_AUTOMOUNT_ENABLED_RE = re.compile(
    r"automatic mounting of new volumes (?:is )?enabled",
    re.IGNORECASE,
)
_WINDOWS_AUTOMOUNT_DISABLED_RE = re.compile(
    r"automatic mounting of new volumes (?:is )?disabled",
    re.IGNORECASE,
)
_WINDOWS_AUTOMOUNT_GUARD_LOCK = threading.Lock()
_WINDOWS_AUTOMOUNT_GUARD_LOCK_ENV = "ARTHEXIS_WINDOWS_AUTOMOUNT_GUARD_LOCK"
_WINDOWS_AUTOMOUNT_GUARD_LOCK_RETRY_SECONDS = 0.25
_WINDOWS_AUTOMOUNT_GUARD_FAILURE_HINT = (
    "Run the image write from an elevated Windows terminal, or retry with "
    "--no-windows-automount-guard only when the target cannot be remounted "
    "during the write."
)
BOOTSTRAP_SYSTEMD_WANTS_PATH = (
    f"{SYSTEMD_MULTI_USER_WANTS_PATH}/arthexis-bootstrap.service"
)
RECOVERY_SYSTEMD_WANTS_PATH = (
    f"{SYSTEMD_MULTI_USER_WANTS_PATH}/arthexis-recovery-access.service"
)
RECOVERY_STALE_FILE_PATHS = (
    "/boot/firmware/userconf.txt",
    "/boot/userconf.txt",
    "/boot/firmware/ssh",
    "/boot/ssh",
    RECOVERY_AUTHORIZED_KEYS_REMOTE_PATH,
    "/usr/local/bin/arthexis-recovery-access.sh",
    RECOVERY_SSHD_CONFIG_REMOTE_PATH,
    RECOVERY_SYSTEMD_SERVICE_PATH,
    RECOVERY_SYSTEMD_WANTS_PATH,
    "/etc/sudoers.d/90-arthexis-recovery",
)


def _render_bootstrap_script(
    *,
    connect_bootstrap_enabled: bool = False,
    bootstrap_user: str = "",
    initial_profile_requires_nftables: bool = False,
) -> str:
    """Render first-boot bootstrap script with image-level feature defaults."""

    default = "1" if connect_bootstrap_enabled else "0"
    script = BOOTSTRAP_SCRIPT.replace(
        'connect_bootstrap_enabled="${ARTHEXIS_ENABLE_CONNECT_BOOTSTRAP:-0}"',
        f'connect_bootstrap_enabled="${{ARTHEXIS_ENABLE_CONNECT_BOOTSTRAP:-{default}}}"',
    )
    script = script.replace(
        "ARTHEXIS_BUNDLE=/usr/local/share/arthexis/arthexis-suite.tar.gz",
        "ARTHEXIS_INITIAL_PROFILE_REQUIRES_NFTABLES="
        f'{"1" if initial_profile_requires_nftables else "0"}\n'
        "ARTHEXIS_BUNDLE=/usr/local/share/arthexis/arthexis-suite.tar.gz",
    )
    if bootstrap_user:
        script = script.replace(
            'for candidate in "${ARTHEXIS_BOOTSTRAP_USER:-}" arthe "${SUDO_USER:-}"; do',
            "for candidate in "
            f'"${{ARTHEXIS_BOOTSTRAP_USER:-}}" {shlex.quote(bootstrap_user)} '
            'arthe "${SUDO_USER:-}"; do',
        )
    return script


RECOVERY_ACCESS_SCRIPT = """#!/usr/bin/env bash
set -euo pipefail

RECOVERY_USER={ssh_user}
RECOVERY_HOME="/home/$RECOVERY_USER"

if ! id -u "$RECOVERY_USER" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash --groups sudo "$RECOVERY_USER"
fi

usermod -aG sudo "$RECOVERY_USER" >/dev/null 2>&1 || true
echo "$RECOVERY_USER ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/90-arthexis-recovery
chmod 0440 /etc/sudoers.d/90-arthexis-recovery

passwd -l "$RECOVERY_USER" >/dev/null 2>&1 || true
install -d -m 700 -o "$RECOVERY_USER" -g "$RECOVERY_USER" "$RECOVERY_HOME/.ssh"
touch "$RECOVERY_HOME/.ssh/authorized_keys"
chown "$RECOVERY_USER:$RECOVERY_USER" "$RECOVERY_HOME/.ssh/authorized_keys"
chmod 600 "$RECOVERY_HOME/.ssh/authorized_keys"
while IFS= read -r recovery_key || [ -n "$recovery_key" ]; do
  [ -n "$recovery_key" ] || continue
  grep -qxF "$recovery_key" "$RECOVERY_HOME/.ssh/authorized_keys" 2>/dev/null || \\
    printf '%s\\n' "$recovery_key" >> "$RECOVERY_HOME/.ssh/authorized_keys"
done < {authorized_keys_path}
systemctl enable ssh
"""

RECOVERY_SSHD_CONFIG = """PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
PubkeyAuthentication yes
PermitRootLogin no
"""

RECOVERY_SYSTEMD_SERVICE = """[Unit]
Description=Arthexis recovery SSH access
DefaultDependencies=no
After=local-fs.target
Before=ssh.service sshd.service arthexis-bootstrap.service
Wants=ssh.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/arthexis-recovery-access.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""

SYSTEMD_SERVICE = """[Unit]
Description=Arthexis first boot bootstrap
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
Environment=ARTHEXIS_GIT_URL={git_url}
ExecStart=/usr/local/bin/arthexis-bootstrap.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""

FIRST_RUN_SCRIPT = """#!/usr/bin/env bash
set -euo pipefail

{recovery_boot_hook}

chmod +x /usr/local/bin/arthexis-bootstrap.sh
systemctl daemon-reload
systemctl enable arthexis-bootstrap.service
systemctl start arthexis-bootstrap.service
rm -f /boot/firstrun.sh /boot/firmware/firstrun.sh
"""

RECOVERY_BOOT_HOOK = """if [ -x /usr/local/bin/arthexis-recovery-access.sh ]; then
  /usr/local/bin/arthexis-recovery-access.sh || \\
    echo "arthexis-recovery-access.sh failed; continuing with bootstrap" >&2
fi"""


@dataclass(frozen=True)
class BuildEngine:
    """Build engine configuration that maps profile names to profile requirements."""

    name: str
    profiles: dict[str, BuildEngineProfile]

    def profile(self, profile_name: str) -> BuildEngineProfile:
        """Return a supported profile or raise a clear operator error."""

        if profile_name not in self.profiles:
            available_profiles = ", ".join(sorted(self.profiles))
            raise ImagerBuildError(
                f"Unsupported profile '{profile_name}' for engine '{self.name}'. Available profiles: {available_profiles}."
            )
        return self.profiles[profile_name]


CONNECT_OTA_PROFILE = BuildEngineProfile(
    name="connect-ota",
    required_base_os="raspberry-pi-os-trixie",
    required_architecture="arm64",
    required_artifacts=(
        "connect-ota-agent",
        "connect-ota-channel-config",
        "connect-ota-device-identity",
    ),
    required_manifest_fields=(
        "release_version",
        "compatibility_model",
        "compatibility_board",
        "ota_channel",
        "ota_artifact_type",
    ),
)

ARTHEXIS_BOOTSTRAP_PROFILE = BuildEngineProfile(
    name="bootstrap",
    required_base_os="",
    required_architecture="",
    required_artifacts=(),
    required_manifest_fields=(),
)

BUILD_ENGINES: dict[str, BuildEngine] = {
    "arthexis-bootstrap": BuildEngine(
        name="arthexis-bootstrap",
        profiles={
            "bootstrap": ARTHEXIS_BOOTSTRAP_PROFILE,
            "connect-ota": CONNECT_OTA_PROFILE,
        },
    ),
}


def normalize_recovery_authorized_key_line(line: str) -> str | None:
    """Normalize one recovery authorized-key line or raise a validation error."""

    normalized = line.strip()
    if not normalized or normalized.startswith("#"):
        return None
    if not VALID_PUBLIC_KEY_PATTERN.match(normalized):
        raise RecoveryAuthorizedKeyError("unrecognized key line")
    try:
        load_ssh_public_key(normalized.encode("utf-8"))
    except (TypeError, ValueError, UnsupportedAlgorithm) as exc:
        raise RecoveryAuthorizedKeyError("malformed public key line") from exc
    return normalized


def _should_exclude_suite_bundle_path(relative_path: Path) -> bool:
    """Return whether a repo path should be excluded from the static image bundle."""

    parts = relative_path.parts
    if not parts:
        return False
    if parts[0] in SUITE_BUNDLE_EXCLUDED_TOP_LEVEL:
        return True
    if any(part in SUITE_BUNDLE_EXCLUDED_NAMES for part in parts):
        return True
    name = relative_path.name
    return (
        name == ".envrc"
        or name.startswith(".env.")
        or name.endswith((".env", ".pyc", ".pyo"))
    )


def _suite_bundle_tarinfo(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo:
    """Normalize bundle metadata needed for Linux first-boot extraction."""

    executable_members = {
        "command.sh",
        "configure.sh",
        "env-refresh.sh",
        "install.sh",
        "manage.py",
        "start.sh",
        "stop.sh",
        "upgrade.sh",
    }
    if tarinfo.name in executable_members or tarinfo.name.startswith("scripts/"):
        tarinfo.mode = 0o755
    return tarinfo


def _create_suite_bundle(
    source_path: Path,
    archive_path: Path,
    *,
    excluded_paths: tuple[Path, ...] = (),
) -> SuiteBundleInfo:
    """Create a sanitized tarball of the suite source for image injection."""

    source = source_path.expanduser().resolve()
    if not source.is_dir():
        raise ImagerBuildError(f"Suite source path is not a directory: {source}")
    for required_file in ("manage.py", "start.sh", "env-refresh.sh", "command.sh"):
        if not (source / required_file).is_file():
            raise ImagerBuildError(
                f"Suite source path is missing required file: {required_file}"
            )
    excluded = {
        path.expanduser().resolve()
        for path in excluded_paths
        if path.expanduser().resolve().is_relative_to(source)
    }

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    file_count = 0
    with tarfile.open(archive_path, "w:gz") as archive:
        for path in sorted(source.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            if path.resolve() in excluded:
                continue
            relative_path = path.relative_to(source)
            if _should_exclude_suite_bundle_path(relative_path):
                continue
            archive.add(
                path,
                arcname=relative_path.as_posix(),
                recursive=False,
                filter=_suite_bundle_tarinfo,
            )
            file_count += 1
    if file_count == 0:
        raise ImagerBuildError(
            f"Suite source path did not contain any bundleable files: {source}"
        )

    return SuiteBundleInfo(
        source_path=source,
        remote_path=SUITE_BUNDLE_REMOTE_PATH,
        sha256=_sha256_for_file(archive_path),
        size_bytes=archive_path.stat().st_size,
        file_count=file_count,
    )


def _normalize_recovery_ssh_access(
    *,
    recovery_ssh_user: str,
    recovery_authorized_keys: list[str] | tuple[str, ...] | None,
) -> RecoverySSHAccess | None:
    """Normalize build input into an optional recovery SSH config."""

    normalized_keys = tuple(
        line.strip() for line in (recovery_authorized_keys or ()) if str(line).strip()
    )

    supplied_username = (recovery_ssh_user or "").strip()
    username = supplied_username or DEFAULT_RECOVERY_SSH_USER
    if not RECOVERY_SSH_USERNAME_PATTERN.fullmatch(username):
        raise ImagerBuildError(f"Invalid recovery SSH username: '{username}'")
    if username in RECOVERY_SSH_FORBIDDEN_USERS:
        raise ImagerBuildError(f"Invalid recovery SSH username: '{username}'")
    if not normalized_keys:
        if supplied_username:
            raise ImagerBuildError(
                "Recovery SSH user was provided without recovery authorized keys. "
                "Provide --recovery-authorized-key-file or omit --recovery-ssh-user."
            )
        return None
    return RecoverySSHAccess(username=username, authorized_keys=normalized_keys)


def _generate_recovery_userconf_password_hash() -> str:
    """Generate an encrypted one-time password for Raspberry Pi OS userconf.txt."""

    password = secrets.token_urlsafe(48)
    salt = secrets.token_hex(8)
    try:
        result = subprocess.run(
            ["openssl", "passwd", "-6", "-salt", salt, "-stdin"],
            input=f"{password}\n",
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ImagerBuildError(
            "openssl is required to generate Raspberry Pi OS recovery userconf.txt."
        ) from exc
    password_hash = result.stdout.strip()
    if result.returncode != 0 or not password_hash.startswith(f"$6${salt}$"):
        detail = (
            result.stderr.strip() or "openssl did not return a SHA-512 password hash"
        )
        raise ImagerBuildError(
            f"Could not generate Raspberry Pi OS recovery userconf.txt password hash: {detail}"
        )
    return password_hash


def _validate_recovery_ap_psk(psk: str) -> str:
    """Return a WPA-PSK passphrase or raise a clear operator error."""

    normalized = psk.strip()
    if not 8 <= len(normalized) <= 63:
        raise ImagerBuildError(
            "Recovery AP PSK must be 8 to 63 printable ASCII characters."
        )
    if any(ord(character) < 32 or ord(character) > 126 for character in normalized):
        raise ImagerBuildError(
            "Recovery AP PSK must be 8 to 63 printable ASCII characters."
        )
    return normalized


def _generate_recovery_ap_psk() -> str:
    """Generate or accept the build-time recovery AP PSK."""

    explicit_psk = (os.environ.get("ARTHEXIS_RECOVERY_AP_PSK") or "").strip()
    if explicit_psk:
        return _validate_recovery_ap_psk(explicit_psk)
    return _validate_recovery_ap_psk(secrets.token_urlsafe(32))


def _write_recovery_ap_psk_sidecar(image_path: Path, psk: str) -> Path:
    """Write the operator-readable recovery AP PSK next to the image artifact."""

    sidecar_path = image_path.with_name(f"{image_path.name}.recovery-ap.psk")
    _write_linux_text(sidecar_path, f"{psk}\n")
    sidecar_path.chmod(0o600)
    return sidecar_path


def _guestfish_run_boot_partition_commands(
    image_path: Path,
    *,
    commands: list[str],
    error_message: str,
) -> None:
    """Run guestfish commands against the Raspberry Pi FAT boot partition."""

    if not commands:
        return

    script = "\n".join(
        [
            "run",
            f"mount {RPI_BOOT_PARTITION_DEVICE} /",
            *commands,
            "umount /",
        ]
    )
    _run_guestfish_raw_script(image_path, script + "\n", error_message=error_message)


def _guestfish_write_boot_partition_file(
    image_path: Path,
    local_path: Path,
    remote_path: str,
) -> None:
    """Upload a file to the Raspberry Pi FAT boot partition."""

    _guestfish_run_boot_partition_commands(
        image_path,
        commands=_guestfish_upload_commands(local_path, remote_path),
        error_message="guestfish failed while writing boot partition files",
    )


def _guestfish_remove_boot_partition_files(
    image_path: Path,
    remote_paths: tuple[str, ...],
) -> None:
    """Remove stale files from the Raspberry Pi FAT boot partition."""

    _guestfish_run_boot_partition_commands(
        image_path,
        commands=[
            _guestfish_remove_file_command(remote_path) for remote_path in remote_paths
        ],
        error_message="guestfish failed while removing boot partition files",
    )


def _write_linux_text(path: Path, content: str) -> None:
    """Write generated Linux-side text without Windows newline translation."""

    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    path.write_text(normalized, encoding="utf-8", newline="\n")


def _customize_image(
    image_path: Path,
    *,
    git_url: str,
    recovery_ssh_access: RecoverySSHAccess | None = None,
    suite_source_path: Path | None = None,
    initial_profile_path: Path | None = None,
    connect_auth_key_path: Path | None = None,
    network_profiles: tuple[NetworkProfileInfo, ...] = (),
    reservation: ImageReservation | None = None,
    connect_bootstrap_enabled: bool = False,
    recovery_ap_psk: str = "",
    initial_profile_requires_nftables: bool = False,
) -> ImageCustomizationResult:
    """Inject bootstrap scripts and systemd units into the image."""

    _ensure_guestfish()
    with TemporaryDirectory(dir=image_path.parent) as temporary_directory:
        work_dir = Path(temporary_directory)
        bootstrap = work_dir / "arthexis-bootstrap.sh"
        service = work_dir / "arthexis-bootstrap.service"
        firstrun = work_dir / "firstrun.sh"
        recovery_service = work_dir / "arthexis-recovery-access.service"
        recovery_ssh_marker = work_dir / "ssh"
        recovery_userconf = work_dir / "userconf.txt"
        reservation_env = work_dir / "reserved-node.env"
        reservation_json = work_dir / "reserved-node.json"
        recovery_ap_psk_file = work_dir / "recovery-ap.psk"
        connect_auth_key_file = (
            _write_connect_auth_key_for_injection(connect_auth_key_path, work_dir)
            if connect_auth_key_path is not None
            else None
        )
        suite_bundle_info: SuiteBundleInfo | None = None
        recovery_ap_psk_path = ""

        _write_linux_text(
            bootstrap,
            _render_bootstrap_script(
                connect_bootstrap_enabled=connect_bootstrap_enabled,
                initial_profile_requires_nftables=initial_profile_requires_nftables,
                bootstrap_user=(
                    recovery_ssh_access.username
                    if recovery_ssh_access
                    and recovery_ssh_access.enabled
                    and recovery_ssh_access.username != DEFAULT_RECOVERY_SSH_USER
                    else ""
                ),
            ),
        )
        _write_linux_text(service, SYSTEMD_SERVICE.format(git_url=git_url))
        _write_linux_text(recovery_service, RECOVERY_SYSTEMD_SERVICE)
        _write_linux_text(
            firstrun,
            FIRST_RUN_SCRIPT.format(
                recovery_boot_hook=(
                    RECOVERY_BOOT_HOOK
                    if recovery_ssh_access and recovery_ssh_access.enabled
                    else ""
                )
            ),
        )

        _guestfish_run_commands(
            image_path,
            [
                *_guestfish_upload_commands(
                    bootstrap,
                    "/usr/local/bin/arthexis-bootstrap.sh",
                    chmod_mode="0755",
                ),
                *_guestfish_upload_commands(service, BOOTSTRAP_SYSTEMD_SERVICE_PATH),
                _guestfish_mkdir_p_command(SYSTEMD_MULTI_USER_WANTS_PATH),
                _guestfish_symlink_command(
                    target=BOOTSTRAP_SYSTEMD_SERVICE_PATH,
                    link_path=BOOTSTRAP_SYSTEMD_WANTS_PATH,
                ),
            ],
            error_message="guestfish failed while injecting bootstrap files",
        )
        if reservation is not None:
            _write_linux_text(reservation_env, render_reservation_env(reservation))
            _write_linux_text(reservation_json, render_reservation_json(reservation))
            _guestfish_run_commands(
                image_path,
                [
                    _guestfish_mkdir_p_command(
                        str(PurePosixPath(RESERVATION_ENV_PATH).parent)
                    ),
                    *_guestfish_upload_commands(
                        reservation_env,
                        RESERVATION_ENV_PATH,
                        chmod_mode="0600",
                    ),
                    *_guestfish_upload_commands(
                        reservation_json,
                        RESERVATION_JSON_PATH,
                        chmod_mode="0644",
                    ),
                ],
                error_message="guestfish failed while injecting reserved node metadata",
            )
        if recovery_ap_psk:
            recovery_ap_psk_path = RECOVERY_AP_PSK_REMOTE_PATH
            _write_linux_text(
                recovery_ap_psk_file,
                f"{_validate_recovery_ap_psk(recovery_ap_psk)}\n",
            )
            _guestfish_run_commands(
                image_path,
                [
                    _guestfish_mkdir_p_command(
                        str(PurePosixPath(RECOVERY_AP_PSK_REMOTE_PATH).parent)
                    ),
                    *_guestfish_upload_commands(
                        recovery_ap_psk_file,
                        RECOVERY_AP_PSK_REMOTE_PATH,
                        chmod_mode="0600",
                    ),
                ],
                error_message="guestfish failed while injecting recovery AP PSK",
            )
        if suite_source_path is not None:
            suite_bundle = work_dir / "arthexis-suite.tar.gz"
            suite_bundle_info = _create_suite_bundle(
                suite_source_path,
                suite_bundle,
                excluded_paths=(initial_profile_path,) if initial_profile_path else (),
            )
            _guestfish_run_commands(
                image_path,
                [
                    _guestfish_mkdir_p_command(
                        str(PurePosixPath(SUITE_BUNDLE_REMOTE_PATH).parent)
                    ),
                    *_guestfish_upload_commands(
                        suite_bundle,
                        SUITE_BUNDLE_REMOTE_PATH,
                        chmod_mode="0644",
                    ),
                ],
                error_message="guestfish failed while injecting suite bundle",
            )
        if initial_profile_path is not None:
            _guestfish_run_commands(
                image_path,
                [
                    _guestfish_mkdir_p_command(
                        str(PurePosixPath(INITIAL_PROFILE_REMOTE_PATH).parent)
                    ),
                    *_guestfish_upload_commands(
                        initial_profile_path,
                        INITIAL_PROFILE_REMOTE_PATH,
                        chmod_mode="0600",
                    ),
                ],
                error_message="guestfish failed while injecting initial profile",
            )
        if connect_auth_key_file is not None:
            _guestfish_run_commands(
                image_path,
                [
                    _guestfish_mkdir_p_command(
                        str(PurePosixPath(CONNECT_AUTH_KEY_REMOTE_PATH).parent)
                    ),
                    *_guestfish_upload_commands(
                        connect_auth_key_file,
                        CONNECT_AUTH_KEY_REMOTE_PATH,
                        chmod_mode="0600",
                    ),
                ],
                error_message="guestfish failed while injecting Raspberry Pi Connect auth key",
            )
        if recovery_ssh_access and recovery_ssh_access.enabled:
            recovery_keys = work_dir / "recovery_authorized_keys"
            recovery_script = work_dir / "arthexis-recovery-access.sh"
            recovery_sshd_config = work_dir / "arthexis-recovery.conf"

            _write_linux_text(
                recovery_keys,
                "\n".join(recovery_ssh_access.authorized_keys) + "\n",
            )
            _write_linux_text(
                recovery_script,
                RECOVERY_ACCESS_SCRIPT.format(
                    ssh_user=shlex.quote(recovery_ssh_access.username),
                    authorized_keys_path=RECOVERY_AUTHORIZED_KEYS_REMOTE_PATH,
                ),
            )
            _write_linux_text(recovery_sshd_config, RECOVERY_SSHD_CONFIG)
            _write_linux_text(
                recovery_userconf,
                f"{recovery_ssh_access.username}:{_generate_recovery_userconf_password_hash()}\n",
            )
            recovery_ssh_marker.write_text("", encoding="utf-8")

            _guestfish_run_commands(
                image_path,
                [
                    _guestfish_mkdir_p_command(
                        str(PurePosixPath(RECOVERY_AUTHORIZED_KEYS_REMOTE_PATH).parent)
                    ),
                    *_guestfish_upload_commands(
                        recovery_keys,
                        RECOVERY_AUTHORIZED_KEYS_REMOTE_PATH,
                        chmod_mode="0600",
                    ),
                    *_guestfish_upload_commands(
                        recovery_script,
                        "/usr/local/bin/arthexis-recovery-access.sh",
                        chmod_mode="0755",
                    ),
                    *_guestfish_upload_commands(
                        recovery_sshd_config,
                        RECOVERY_SSHD_CONFIG_REMOTE_PATH,
                        chmod_mode="0644",
                    ),
                    *_guestfish_upload_commands(
                        recovery_service,
                        RECOVERY_SYSTEMD_SERVICE_PATH,
                        chmod_mode="0644",
                    ),
                    _guestfish_symlink_command(
                        target=RECOVERY_SYSTEMD_SERVICE_PATH,
                        link_path=RECOVERY_SYSTEMD_WANTS_PATH,
                    ),
                ],
                error_message="guestfish failed while injecting recovery files",
            )
            _guestfish_write_boot_partition_file(
                image_path,
                recovery_userconf,
                RECOVERY_BOOT_USERCONF_PATH,
            )
            _guestfish_write_boot_partition_file(
                image_path,
                recovery_ssh_marker,
                RECOVERY_BOOT_SSH_MARKER_PATH,
            )
        else:
            _guestfish_run_commands(
                image_path,
                [
                    _guestfish_remove_file_command(stale_file_path)
                    for stale_file_path in RECOVERY_STALE_FILE_PATHS
                ],
                error_message="guestfish failed while removing stale recovery files",
            )
            _guestfish_remove_boot_partition_files(
                image_path,
                (RECOVERY_BOOT_USERCONF_PATH, RECOVERY_BOOT_SSH_MARKER_PATH),
            )
        if network_profiles:
            profile_commands = [
                _guestfish_mkdir_p_command(NETWORK_MANAGER_CONNECTIONS_REMOTE_PATH)
            ]
            for profile in network_profiles:
                profile_commands.extend(
                    _guestfish_upload_commands(
                        profile.source_path,
                        profile.remote_path,
                        chmod_mode="0600",
                    )
                )
            _guestfish_run_commands(
                image_path,
                profile_commands,
                error_message="guestfish failed while injecting host network profiles",
            )
        _guestfish_write_boot_partition_file(image_path, firstrun, "/firstrun.sh")

    return ImageCustomizationResult(
        suite_bundle=suite_bundle_info,
        network_profiles=network_profiles,
        reservation=reservation,
        recovery_ap_psk_path=recovery_ap_psk_path,
    )


def prepare_image_serve(
    *,
    artifact_name: str = "",
    image_path: str = "",
    host: str = "0.0.0.0",
    port: int = 8088,
    url_host: str = "",
    base_url: str = "",
    update_artifact_url: bool = True,
) -> ServeResult:
    """Resolve an image and optionally persist the URL used for local artifact serving."""

    resolved_path, artifact = _resolve_image_path_for_write(
        artifact_name=artifact_name,
        image_path=image_path,
    )
    artifact_url = _build_served_artifact_url(
        output_filename=resolved_path.name,
        port=port,
        url_host=url_host,
        base_url=base_url,
    )
    if artifact is not None and update_artifact_url:
        artifact.download_uri = artifact_url
        artifact.metadata = {
            **artifact.metadata,
            "local_serve": {
                "host": host,
                "port": port,
                "url": artifact_url,
                "updated_at": timezone.now().isoformat(),
            },
        }
        artifact.save(update_fields=["download_uri", "metadata", "updated_at"])
    return ServeResult(
        image_path=resolved_path,
        url=artifact_url,
        host=host,
        port=port,
    )


def serve_image_file(*, image_path: Path, host: str, port: int) -> None:
    """Serve a single image file over HTTP until interrupted."""

    image = image_path.resolve()
    filename = image.name

    class SingleImageHandler(BaseHTTPRequestHandler):
        def do_HEAD(self) -> None:  # noqa: N802
            self._send_file(include_body=False)

        def do_GET(self) -> None:  # noqa: N802
            self._send_file(include_body=True)

        def _send_file(self, *, include_body: bool) -> None:
            requested_name = Path(unquote(urlparse(self.path).path).lstrip("/")).name
            if requested_name != filename:
                self.send_error(404, "Image artifact not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(image.stat().st_size))
            self.send_header(
                "Content-Disposition", f'attachment; filename="{filename}"'
            )
            self.end_headers()
            if include_body:
                with image.open("rb") as handle:
                    shutil.copyfileobj(handle, self.wfile, length=1024 * 1024)

        def log_message(self, _format: str, *args: object) -> None:
            return

    with ThreadingHTTPServer((host, port), SingleImageHandler) as server:
        server.serve_forever()


def _tcp_access_check(
    *, host: str, port: int, timeout: float, name: str
) -> AccessCheckResult:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return AccessCheckResult(name=name, ok=True, detail=f"tcp/{port} reachable")
    except OSError as exc:
        return AccessCheckResult(
            name=name, ok=False, detail=f"tcp/{port} failed: {exc}"
        )


def _ssh_access_check(
    *,
    host: str,
    user: str,
    port: int,
    key_path: str,
    timeout: float,
) -> AccessCheckResult:
    ssh_path = shutil.which("ssh")
    if not ssh_path:
        return AccessCheckResult(
            name="ssh-auth", ok=False, detail="ssh command not found"
        )

    command = [
        ssh_path,
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"ConnectTimeout={max(1, int(timeout))}",
        "-p",
        str(port),
    ]
    if key_path:
        command.extend(["-i", str(Path(key_path).expanduser())])
    command.extend([f"{user}@{host}", "true"])
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=max(1, int(timeout) + 2),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return AccessCheckResult(name="ssh-auth", ok=False, detail=f"ssh failed: {exc}")
    if result.returncode == 0:
        return AccessCheckResult(
            name="ssh-auth", ok=True, detail=f"{user}@{host}:{port} accepted key auth"
        )
    detail = (
        (result.stderr or result.stdout or "ssh command failed").strip().splitlines()
    )
    return AccessCheckResult(
        name="ssh-auth", ok=False, detail=detail[-1] if detail else "ssh command failed"
    )


def _http_access_check(*, url: str, timeout: float) -> AccessCheckResult:
    try:
        with urlopen(Request(url), timeout=timeout) as response:
            status = response.getcode()
    except OSError as exc:
        return AccessCheckResult(name="http", ok=False, detail=f"{url} failed: {exc}")
    return AccessCheckResult(
        name="http",
        ok=200 <= status < 500,
        detail=f"{url} returned HTTP {status}",
    )


def test_rpi_access(
    *,
    host: str,
    ssh_user: str = DEFAULT_RECOVERY_SSH_USER,
    ssh_port: int = 22,
    ssh_key: str = "",
    http_url: str = "",
    http_port: int = 8888,
    timeout: float = 5.0,
    skip_ssh: bool = False,
    skip_http: bool = False,
) -> RpiAccessTestResult:
    """Test SSH and HTTP access to a burned Raspberry Pi image."""

    checks: list[AccessCheckResult] = []
    if not skip_ssh:
        checks.append(
            _tcp_access_check(host=host, port=ssh_port, timeout=timeout, name="ssh-tcp")
        )
        checks.append(
            _ssh_access_check(
                host=host,
                user=ssh_user,
                port=ssh_port,
                key_path=ssh_key,
                timeout=timeout,
            )
        )
    if not skip_http:
        target_url = http_url or _build_local_http_url(host=host, port=http_port)
        checks.append(_http_access_check(url=target_url, timeout=timeout))
    if not checks:
        raise ImagerBuildError("Enable at least one access check.")
    return RpiAccessTestResult(host=host, checks=tuple(checks))


def _resolve_root_disk_path() -> str | None:
    """Resolve the current host root disk block path, if discoverable."""

    try:
        findmnt_result = subprocess.run(
            ["findmnt", "-n", "-o", "SOURCE", "/"],
            capture_output=True,
            text=True,
            check=False,
        )
        root_source = findmnt_result.stdout.strip()
        if findmnt_result.returncode != 0 or not root_source:
            return None

        current_path = root_source
        visited_paths: set[str] = set()
        while current_path and current_path not in visited_paths:
            visited_paths.add(current_path)
            info_result = subprocess.run(
                ["lsblk", "-n", "-o", "TYPE,PKNAME", current_path],
                capture_output=True,
                text=True,
                check=False,
            )
            if info_result.returncode != 0:
                return None
            info = info_result.stdout.strip().splitlines()
            if not info:
                return None
            parts = info[0].split(maxsplit=1)
            device_type = parts[0]
            parent_kernel_name = parts[1] if len(parts) > 1 else ""

            if device_type == "disk":
                return current_path
            if not parent_kernel_name:
                return None

            current_path = f"/dev/{parent_kernel_name}"
    except FileNotFoundError:
        return None
    return None


def _stable_identity_paths_for_device(device_path: str) -> list[str]:
    """Return stable /dev/disk/by-id aliases that currently point at a disk."""

    if os.name == "nt" or not device_path:
        return []
    by_id_dir = Path("/dev/disk/by-id")
    if not by_id_dir.exists():
        return []
    try:
        device_resolved = Path(device_path).resolve(strict=False)
    except OSError:
        return []

    identity_paths: list[str] = []
    try:
        candidates = sorted(by_id_dir.iterdir())
    except OSError:
        return []
    for candidate in candidates:
        try:
            if candidate.resolve(strict=False) == device_resolved:
                identity_paths.append(str(candidate))
        except OSError:
            continue
    return identity_paths


def _walk_block_descendants(entry: dict[str, object]) -> list[dict[str, object]]:
    """Return all descendants from an lsblk tree row."""

    descendants: list[dict[str, object]] = []
    for child in entry.get("children") or []:
        if not isinstance(child, dict):
            continue
        descendants.append(child)
        descendants.extend(_walk_block_descendants(child))
    return descendants


def _coerce_windows_json_rows(value: object) -> list[dict[str, object]]:
    """Normalize PowerShell ConvertTo-Json array/singleton output."""

    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _coerce_windows_access_paths(value: object) -> list[str]:
    """Normalize Windows partition access paths from PowerShell JSON."""

    if value is None:
        return []
    if isinstance(value, str):
        normalized = value.strip()
        return [normalized] if normalized else []
    if isinstance(value, list):
        paths = [str(item).strip() for item in value]
        return [path for path in paths if path]
    return []


def _is_windows_volume_guid_access_path(value: str) -> bool:
    """Return whether an access path is only a Windows volume GUID path."""

    return bool(_WINDOWS_VOLUME_GUID_ACCESS_PATH_RE.match(value.strip()))


def _is_windows_physical_drive_path(value: str) -> bool:
    """Return whether a path targets a Windows raw physical drive."""

    return bool(_WINDOWS_PHYSICAL_DRIVE_PATH_RE.match(value.strip()))


def _windows_mountpoints_from_partition(partition: dict[str, object]) -> list[str]:
    """Return operator-visible Windows mountpoints for a disk partition."""

    mountpoints: list[str] = []
    drive_letter = str(partition.get("DriveLetter") or "").strip()
    if drive_letter:
        mountpoints.append(f"{drive_letter.upper()}:\\")
    mountpoints.extend(
        access_path
        for access_path in _coerce_windows_access_paths(partition.get("AccessPaths"))
        if not _is_windows_volume_guid_access_path(access_path)
    )
    return mountpoints


def _windows_system32_tool_path(tool_name: str) -> str:
    system_root = (
        os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
    )
    return str(PureWindowsPath(system_root) / "System32" / tool_name)


def _windows_automount_guard_lock_path() -> Path:
    configured = os.environ.get(_WINDOWS_AUTOMOUNT_GUARD_LOCK_ENV)
    if configured:
        return Path(configured)
    if os.name == "nt":
        program_data = os.environ.get("ProgramData") or r"C:\ProgramData"
        return (
            Path(program_data) / "Arthexis" / "locks" / "windows-automount-guard.lock"
        )
    return Path.home() / ".cache" / "arthexis" / "windows-automount-guard.lock"


def _lock_windows_automount_guard_file(lock_file) -> None:
    while True:
        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError:
            time.sleep(_WINDOWS_AUTOMOUNT_GUARD_LOCK_RETRY_SECONDS)


@contextmanager
def _windows_automount_guard_lock():
    lock_path = _windows_automount_guard_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _WINDOWS_AUTOMOUNT_GUARD_LOCK:
        with lock_path.open("a+b") as lock_file:
            lock_file.seek(0)
            if not lock_file.read(1):
                lock_file.write(b"0")
                lock_file.flush()
            lock_file.seek(0)
            if os.name == "nt":
                _lock_windows_automount_guard_file(lock_file)
            else:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                lock_file.seek(0)
                if os.name == "nt":
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _run_windows_command_for_automount(
    args: list[str], *, action: str
) -> subprocess.CompletedProcess[str]:
    """Run one Windows automount guard command and raise an operator-readable error."""

    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        command = " ".join(args)
        raise ImagerBuildError(
            f"Windows automount guard could not {action}; '{args[0]}' was not found. "
            f"Command: {command}. {_WINDOWS_AUTOMOUNT_GUARD_FAILURE_HINT}"
        ) from exc
    if result.returncode == 0:
        return result
    command = " ".join(args)
    detail = (result.stderr or result.stdout or "").strip()
    message = f"Windows automount guard could not {action}. Command failed: {command}."
    if detail:
        message = f"{message} {detail}"
    raise ImagerBuildError(f"{message} {_WINDOWS_AUTOMOUNT_GUARD_FAILURE_HINT}")


def _run_windows_diskpart_automount(
    command: str, *, action: str
) -> subprocess.CompletedProcess[str]:
    """Run a diskpart automount command from a temporary script file."""

    script_path = ""
    try:
        with NamedTemporaryFile(
            "w",
            encoding="ascii",
            delete=False,
            suffix=".txt",
        ) as script_file:
            script_file.write(f"{command}\nexit\n")
            script_path = script_file.name
        return _run_windows_command_for_automount(
            [_windows_system32_tool_path("diskpart.exe"), "/s", script_path],
            action=action,
        )
    finally:
        if script_path:
            try:
                os.unlink(script_path)
            except OSError:
                pass


def _get_windows_automount_enabled() -> bool:
    """Return the current Windows automount state reported by diskpart."""

    result = _run_windows_diskpart_automount("automount", action="read automount state")
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    if _WINDOWS_AUTOMOUNT_DISABLED_RE.search(output):
        return False
    if _WINDOWS_AUTOMOUNT_ENABLED_RE.search(output):
        return True
    raise ImagerBuildError(
        "Windows automount guard could not read the current automount state "
        f"from diskpart output. {_WINDOWS_AUTOMOUNT_GUARD_FAILURE_HINT}"
    )


def _set_windows_automount_enabled(enabled: bool) -> None:
    """Enable or disable Windows automount through both supported system tools."""

    mountvol_switch = "/E" if enabled else "/N"
    diskpart_command = "automount enable" if enabled else "automount disable"
    action = "restore automount" if enabled else "disable automount"
    _run_windows_command_for_automount(
        [_windows_system32_tool_path("mountvol.exe"), mountvol_switch],
        action=action,
    )
    _run_windows_diskpart_automount(diskpart_command, action=action)


def _raise_with_windows_automount_restore_failure(
    original_exc: BaseException, restore_exc: ImagerBuildError
) -> None:
    detail = (
        "Windows automount guard also could not restore the previous automount "
        f"state: {restore_exc}"
    )
    if isinstance(original_exc, ImagerBuildError):
        raise ImagerBuildError(f"{original_exc} {detail}") from restore_exc
    raise ImagerBuildError(
        f"Image write was interrupted by {type(original_exc).__name__}. {detail}"
    ) from restore_exc


@contextmanager
def _windows_automount_guard(enabled: bool):
    """Temporarily prevent Windows from remounting media during raw image writes."""

    if not enabled:
        yield
        return

    with _windows_automount_guard_lock():
        previous_automount_enabled = _get_windows_automount_enabled()

        try:
            _set_windows_automount_enabled(False)
        except ImagerBuildError as exc:
            try:
                _set_windows_automount_enabled(previous_automount_enabled)
            except ImagerBuildError as restore_exc:
                _raise_with_windows_automount_restore_failure(exc, restore_exc)
            raise

        try:
            yield
        except BaseException as exc:
            try:
                _set_windows_automount_enabled(previous_automount_enabled)
            except ImagerBuildError as restore_exc:
                _raise_with_windows_automount_restore_failure(exc, restore_exc)
            raise
        else:
            _set_windows_automount_enabled(previous_automount_enabled)


def _normalize_media_identity(value: object) -> str:
    """Normalize block-device identity text from platform inventory tools."""

    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _device_write_blocked_reason(
    *, vendor: object, model: object, serial: object
) -> str:
    """Return the destructive-write block reason for protected media identities."""

    identity = " ".join(
        part
        for part in (
            _normalize_media_identity(vendor),
            _normalize_media_identity(model),
            _normalize_media_identity(serial),
        )
        if part
    )
    for vendor_token, model_token, reason in _BLOCKED_WRITE_MEDIA:
        if vendor_token in identity and model_token in identity:
            return reason
    return ""


def _list_windows_block_devices() -> list[BlockDeviceInfo]:
    """Enumerate Windows physical disks with safety metadata."""

    powershell = shutil.which("powershell") or shutil.which("powershell.exe")
    if not powershell:
        raise ImagerBuildError("PowerShell is required to enumerate Windows disks.")
    script = (
        "$ErrorActionPreference='Stop';"
        "$disks=@(Get-Disk | Select-Object Number,FriendlyName,SerialNumber,BusType,Size,IsBoot,IsSystem,IsReadOnly,IsOffline,OperationalStatus);"
        "$partitions=@(Get-Partition | Select-Object DiskNumber,PartitionNumber,DriveLetter,AccessPaths);"
        "[pscustomobject]@{disks=$disks;partitions=$partitions} | ConvertTo-Json -Depth 6 -Compress"
    )
    try:
        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ImagerBuildError(
            "PowerShell is required to enumerate Windows disks."
        ) from exc
    if result.returncode != 0:
        raise ImagerBuildError(
            result.stderr.strip() or "Unable to enumerate Windows disks."
        )
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ImagerBuildError(
            "Unable to parse Windows disk inventory output."
        ) from exc

    partitions_by_disk: dict[int, list[dict[str, object]]] = {}
    for partition in _coerce_windows_json_rows(payload.get("partitions")):
        try:
            disk_number = int(partition.get("DiskNumber"))
        except (TypeError, ValueError):
            continue
        partitions_by_disk.setdefault(disk_number, []).append(partition)

    devices: list[BlockDeviceInfo] = []
    for disk in _coerce_windows_json_rows(payload.get("disks")):
        try:
            number = int(disk.get("Number"))
            size_bytes = int(disk.get("Size") or 0)
        except (TypeError, ValueError):
            continue

        bus_type = str(disk.get("BusType") or "")
        vendor = ""
        model = str(disk.get("FriendlyName") or "")
        serial = str(disk.get("SerialNumber") or "")
        disk_partitions = partitions_by_disk.get(number, [])
        mountpoints: list[str] = []
        partitions: list[str] = []
        for partition in disk_partitions:
            partition_number = partition.get("PartitionNumber")
            if partition_number not in (None, ""):
                partitions.append(f"PhysicalDrive{number}Partition{partition_number}")
            mountpoints.extend(_windows_mountpoints_from_partition(partition))

        devices.append(
            BlockDeviceInfo(
                path=f"\\\\.\\PhysicalDrive{number}",
                size_bytes=size_bytes,
                transport=bus_type,
                removable=bus_type.lower() in {"usb", "sd", "mmc"},
                mountpoints=sorted(set(mountpoints)),
                partitions=partitions,
                protected=bool(disk.get("IsBoot") or disk.get("IsSystem")),
                vendor=vendor,
                model=model,
                serial=serial,
                write_blocked_reason=_device_write_blocked_reason(
                    vendor=vendor,
                    model=model,
                    serial=serial,
                ),
            )
        )
    return sorted(devices, key=lambda item: item.path)


def list_block_devices() -> list[BlockDeviceInfo]:
    """Enumerate host block devices and safety-relevant metadata."""

    if os.name == "nt":
        return _list_windows_block_devices()

    try:
        result = subprocess.run(
            [
                "lsblk",
                "-J",
                "-b",
                "--tree",
                "-o",
                "PATH,SIZE,RM,TRAN,TYPE,MOUNTPOINTS,VENDOR,MODEL,SERIAL",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ImagerBuildError(
            "The 'lsblk' command is required but was not found."
        ) from exc
    if result.returncode != 0:
        raise ImagerBuildError(
            result.stderr.strip() or "Unable to enumerate block devices."
        )
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ImagerBuildError(
            "Unable to parse lsblk output for device discovery."
        ) from exc

    root_disk = _resolve_root_disk_path()
    devices: list[BlockDeviceInfo] = []
    for entry in payload.get("blockdevices", []):
        if entry.get("type") != "disk":
            continue
        descendants = _walk_block_descendants(entry)
        mountpoints = [mount for mount in (entry.get("mountpoints") or []) if mount]
        mountpoints.extend(
            mount
            for child in descendants
            for mount in (child.get("mountpoints") or [])
            if mount
        )
        normalized_mountpoints = sorted(set(mountpoints))
        partitions = [
            child.get("path", "") for child in descendants if child.get("path")
        ]
        vendor = str(entry.get("vendor") or "")
        model = str(entry.get("model") or "")
        serial = str(entry.get("serial") or "")
        path = str(entry.get("path", ""))
        devices.append(
            BlockDeviceInfo(
                path=path,
                size_bytes=int(entry.get("size") or 0),
                transport=str(entry.get("tran") or ""),
                removable=bool(entry.get("rm")),
                mountpoints=normalized_mountpoints,
                partitions=partitions,
                protected=path == root_disk or "/" in normalized_mountpoints,
                vendor=vendor,
                model=model,
                serial=serial,
                identity_paths=_stable_identity_paths_for_device(path),
                write_blocked_reason=_device_write_blocked_reason(
                    vendor=vendor,
                    model=model,
                    serial=serial,
                ),
            )
        )
    return sorted(devices, key=lambda item: item.path)


def _resolve_image_path_for_write(
    *, artifact_name: str, image_path: str
) -> tuple[Path, RaspberryPiImageArtifact | None]:
    """Resolve CLI write source from artifact registry or explicit image path."""

    if artifact_name:
        artifact = RaspberryPiImageArtifact.objects.filter(name=artifact_name).first()
        if artifact is None:
            raise ImagerBuildError(f"Artifact '{artifact_name}' does not exist.")
        resolved_path = Path(artifact.output_path).expanduser().resolve()
        if not resolved_path.exists():
            raise ImagerBuildError(
                f"Artifact image file does not exist: {resolved_path}"
            )
        return resolved_path, artifact
    resolved_path = Path(image_path).expanduser().resolve()
    if not resolved_path.exists():
        raise ImagerBuildError(f"Image file does not exist: {resolved_path}")
    return resolved_path, None


def _confirm_destructive_write(
    *, device_path: str, image_path: Path, size_bytes: int, confirmed: bool
) -> None:
    """Require explicit operator confirmation before device overwrite."""

    if confirmed:
        return
    raise ImagerBuildError(
        "Refusing write without explicit confirmation. Re-run with --yes.\n"
        f"Planned overwrite target: {device_path}\n"
        f"Source image: {image_path}\n"
        f"Bytes to write: {size_bytes}"
    )


def _resolve_write_backup_dir(backup_dir: Path | str | None) -> Path:
    """Resolve the directory used for pre-write removable media backups."""

    if backup_dir is None or not str(backup_dir).strip():
        resolved = Path(settings.BASE_DIR) / DEFAULT_IMAGE_WRITE_BACKUP_DIR
    else:
        resolved = Path(backup_dir).expanduser()
        if not resolved.is_absolute():
            resolved = Path(settings.BASE_DIR) / resolved
    return resolved.resolve(strict=False)


def _safe_backup_device_name(device_path: str) -> str:
    """Return a filesystem-safe label for a block device backup filename."""

    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", device_path.strip()).strip("-._")
    return normalized or "device"


def _fsync_directory_entry(directory_path: Path) -> None:
    """Flush a directory entry update when the platform supports directory fsync."""

    if os.name == "nt":
        return
    try:
        directory_fd = os.open(directory_path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    except OSError:
        pass
    finally:
        os.close(directory_fd)


def _backup_device_before_write(
    *,
    device_path: str,
    target_device: BlockDeviceInfo,
    backup_dir: Path | str | None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> WriteBackupResult:
    """Capture and verify the current target media before overwriting it."""

    backup_size = target_device.size_bytes
    if backup_size <= 0:
        raise ImagerBuildError(
            f"Cannot back up target device '{device_path}': discovered size is not positive."
        )

    resolved_dir = _resolve_write_backup_dir(backup_dir)
    try:
        resolved_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ImagerBuildError(
            f"Could not create image-write backup directory '{resolved_dir}': {exc}"
        ) from exc
    if not resolved_dir.is_dir():
        raise ImagerBuildError(
            f"Image-write backup path is not a directory: {resolved_dir}"
        )

    available_bytes = shutil.disk_usage(resolved_dir).free
    if available_bytes < backup_size:
        raise ImagerBuildError(
            f"Insufficient free space for image-write backup in '{resolved_dir}': "
            f"need {backup_size} bytes, available {available_bytes} bytes."
        )

    timestamp = timezone.now().strftime("%Y%m%dT%H%M%S%f")
    backup_path = (
        resolved_dir
        / f"{_safe_backup_device_name(device_path)}-{timestamp}-{backup_size}.img"
    )
    temporary_path = backup_path.with_name(f"{backup_path.name}.tmp")
    hasher = hashlib.sha256()
    remaining_bytes = backup_size
    copied_bytes = 0
    chunk_size = 1024 * 1024 * 4

    try:
        with (
            open(device_path, "rb", buffering=0) as device_handle,
            os.fdopen(
                os.open(
                    temporary_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                ),
                "wb",
                buffering=0,
            ) as backup_handle,
        ):
            while remaining_bytes:
                chunk = device_handle.read(min(chunk_size, remaining_bytes))
                if not chunk:
                    raise ImagerBuildError(
                        f"Could not read {backup_size} bytes from target device "
                        f"'{device_path}' for backup."
                    )
                backup_handle.write(chunk)
                hasher.update(chunk)
                remaining_bytes -= len(chunk)
                copied_bytes += len(chunk)
                if progress_callback is not None:
                    progress_callback(copied_bytes, backup_size)
            backup_handle.flush()
            os.fsync(backup_handle.fileno())
        temporary_path.replace(backup_path)
        _fsync_directory_entry(backup_path.parent)
    except ImagerBuildError:
        temporary_path.unlink(missing_ok=True)
        raise
    except OSError as exc:
        temporary_path.unlink(missing_ok=True)
        raise ImagerBuildError(
            f"Could not create image-write backup for '{device_path}': {exc}"
        ) from exc

    expected_sha256 = hasher.hexdigest()
    try:
        actual_size = backup_path.stat().st_size
    except OSError as exc:
        raise ImagerBuildError(
            f"Could not stat image-write backup '{backup_path}': {exc}"
        ) from exc
    if actual_size != backup_size:
        raise ImagerBuildError(
            f"Image-write backup '{backup_path}' size mismatch: "
            f"expected {backup_size} bytes, got {actual_size} bytes."
        )
    verified_sha256 = _sha256_for_file(
        backup_path,
        progress_callback=progress_callback,
    )
    if verified_sha256 != expected_sha256:
        raise ImagerBuildError(
            f"Image-write backup verification failed for '{backup_path}'."
        )

    return WriteBackupResult(
        path=backup_path,
        size_bytes=backup_size,
        sha256=verified_sha256,
        verified=True,
    )


def _format_bytes_per_second(rate: float) -> str:
    """Return a compact human-readable byte rate."""

    units = ("B/s", "KiB/s", "MiB/s", "GiB/s")
    value = float(rate)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} {units[-1]}"


def _copy_image_to_device_with_speed_guard(
    *,
    source_handle,
    device_handle,
    device_path: str,
    source_size: int,
    min_write_rate_bytes_per_second: float,
    write_rate_grace_seconds: float,
    progress_callback: Callable[[int, int], None] | None = None,
) -> None:
    """Copy image bytes while aborting clearly bad removable-media throughput."""

    started_at = time.monotonic()
    written_bytes = 0
    while True:
        chunk = source_handle.read(DEFAULT_IMAGE_WRITE_CHUNK_SIZE_BYTES)
        if not chunk:
            return
        device_handle.write(chunk)
        written_bytes += len(chunk)
        if progress_callback is not None:
            progress_callback(written_bytes, source_size)
        if written_bytes >= source_size:
            return

        elapsed_seconds = time.monotonic() - started_at
        if (
            min_write_rate_bytes_per_second <= 0
            or elapsed_seconds < write_rate_grace_seconds
            or elapsed_seconds <= 0
        ):
            continue
        observed_rate = written_bytes / elapsed_seconds
        if observed_rate >= min_write_rate_bytes_per_second:
            continue

        raise ImagerBuildError(
            "Image write throughput is too slow "
            f"({_format_bytes_per_second(observed_rate)} after "
            f"{written_bytes} of {source_size} bytes). Aborting burn for "
            f"'{device_path}'; the burner may be improperly connected or attached "
            "through a slow or unstable USB hub. Connect the burner directly to USB "
            "and retry."
        )


def _get_write_target_device(*, device_path: str, source_size: int) -> BlockDeviceInfo:
    devices: dict[str, BlockDeviceInfo] = {}
    for device in list_block_devices():
        devices[device.path] = device
        for identity_path in device.identity_paths:
            devices[identity_path] = device
    if device_path not in devices:
        raise ImagerBuildError(
            f"Target device '{device_path}' was not found in discovered block devices."
        )
    target_device = devices[device_path]

    if target_device.protected:
        raise ImagerBuildError(
            f"Refusing to overwrite protected system/root disk: {device_path}"
        )
    if target_device.write_blocked_reason:
        raise ImagerBuildError(
            f"Refusing to overwrite blocked media '{device_path}': "
            f"{target_device.write_blocked_reason}"
        )
    if target_device.mountpoints:
        mounts = ", ".join(target_device.mountpoints)
        raise ImagerBuildError(
            f"Refusing to overwrite mounted device '{device_path}'. Unmount all partitions first: {mounts}"
        )
    if target_device.size_bytes < source_size:
        raise ImagerBuildError(
            f"Target device '{device_path}' is too small ({target_device.size_bytes} bytes) for image size {source_size} bytes."
        )

    return target_device


def write_image_to_device(
    *,
    device_path: str,
    artifact_name: str = "",
    image_path: str = "",
    confirmed: bool = False,
    backup: bool = False,
    backup_dir: Path | str | None = None,
    min_write_rate_bytes_per_second: float = (
        DEFAULT_IMAGE_WRITE_MIN_RATE_BYTES_PER_SECOND
    ),
    write_rate_grace_seconds: float = DEFAULT_IMAGE_WRITE_SPEED_GRACE_SECONDS,
    progress_callback: Callable[[int, int], None] | None = None,
    windows_automount_guard: bool | None = None,
) -> WriteResult:
    """Write an artifact/local image to a block device with safety checks and verification."""

    if bool(artifact_name) == bool(image_path):
        raise ImagerBuildError("Provide exactly one of artifact_name or image_path.")

    source_path, artifact = _resolve_image_path_for_write(
        artifact_name=artifact_name,
        image_path=image_path,
    )
    source_size = source_path.stat().st_size
    target_device = _get_write_target_device(
        device_path=device_path,
        source_size=source_size,
    )

    _confirm_destructive_write(
        device_path=device_path,
        image_path=source_path,
        size_bytes=source_size,
        confirmed=confirmed,
    )

    guard_enabled = (
        os.name == "nt" and _is_windows_physical_drive_path(device_path)
        if windows_automount_guard is None
        else bool(windows_automount_guard)
    )

    with _windows_automount_guard(guard_enabled):
        if guard_enabled:
            target_device = _get_write_target_device(
                device_path=device_path,
                source_size=source_size,
            )
        backup_result = None
        if backup:
            backup_result = _backup_device_before_write(
                device_path=device_path,
                target_device=target_device,
                backup_dir=backup_dir,
                progress_callback=progress_callback,
            )

        source_hash = _sha256_for_file(
            source_path,
            progress_callback=progress_callback,
        )
        with (
            source_path.open("rb") as source_handle,
            open(device_path, "r+b", buffering=0) as device_handle,
        ):
            _copy_image_to_device_with_speed_guard(
                source_handle=source_handle,
                device_handle=device_handle,
                device_path=device_path,
                source_size=source_size,
                min_write_rate_bytes_per_second=min_write_rate_bytes_per_second,
                write_rate_grace_seconds=write_rate_grace_seconds,
                progress_callback=progress_callback,
            )
            device_handle.flush()
            os.fsync(device_handle.fileno())
        write_hash = _sha256_for_prefix(
            Path(device_path),
            size_bytes=source_size,
            progress_callback=progress_callback,
        )
    verified = source_hash == write_hash
    if not verified:
        raise ImagerBuildError(
            f"Verification failed for '{device_path}': checksum mismatch after write."
        )

    if artifact is not None:
        last_write = {
            "device_path": device_path,
            "source_path": str(source_path),
            "size_bytes": source_size,
            "sha256": source_hash,
            "verified": True,
            "verified_at": timezone.now().isoformat(),
        }
        if backup_result is not None:
            last_write["backup"] = {
                "path": str(backup_result.path),
                "size_bytes": backup_result.size_bytes,
                "sha256": backup_result.sha256,
                "verified": backup_result.verified,
            }
        artifact.metadata = {
            **artifact.metadata,
            "last_write": last_write,
        }
        artifact.save(update_fields=["metadata", "updated_at"])

    return WriteResult(
        device_path=device_path,
        image_path=source_path,
        size_bytes=source_size,
        source_sha256=source_hash,
        written_sha256=write_hash,
        verified=verified,
        backup=backup_result,
    )


def build_rpi4b_image(
    *,
    name: str,
    base_image_uri: str,
    output_dir: Path,
    download_base_uri: str,
    git_url: str,
    customize: bool = True,
    build_engine: str = "arthexis-bootstrap",
    profile: str = "bootstrap",
    profile_metadata: dict[str, object] | None = None,
    recovery_ssh_user: str = "",
    recovery_authorized_keys: list[str] | tuple[str, ...] | None = None,
    skip_recovery_ssh: bool = False,
    bundle_suite: bool = True,
    suite_source_path: Path | None = None,
    initial_profile_path: Path | None = None,
    connect_auth_key_path: Path | None = None,
    copy_all_host_networks: bool = False,
    host_network_names: list[str] | tuple[str, ...] | None = None,
    host_network_profile_dir: Path | None = None,
    copy_parent_networks: bool = False,
    reserve_node: bool = False,
    reserve_hostname_prefix: str = "",
    reserve_number: int | None = None,
    reserve_role: str = "",
    next_number_base_url: str = "",
    downstream_registration_base_url: str = "",
    reservation_claim_token: str = "",
    connect_bootstrap_enabled: bool = False,
    skip_connect_bootstrap: bool = False,
    minimum_image_size_bytes: int | None = None,
    storage_backend: str = STORAGE_BACKEND_LOCAL,
    storage_options: dict[str, object] | None = None,
) -> BuildResult:
    """Build and register a Raspberry Pi 4B Arthexis image artifact."""

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise ImagerBuildError(
            "Artifact name must start with an alphanumeric character and use only letters, numbers, dot, underscore, or hyphen."
        )
    normalized_storage_backend = (
        storage_backend or ""
    ).strip().lower() or STORAGE_BACKEND_LOCAL
    if normalized_storage_backend not in SUPPORTED_STORAGE_BACKENDS:
        supported_backends = ", ".join(sorted(SUPPORTED_STORAGE_BACKENDS))
        raise ImagerBuildError(
            f"Unsupported storage backend '{storage_backend}'. Available backends: {supported_backends}."
        )
    normalized_storage_options = dict(storage_options or {})
    effective_minimum_image_size_bytes = _normalize_minimum_image_size_bytes(
        minimum_image_size_bytes,
        customize=customize,
    )

    engine = BUILD_ENGINES.get(build_engine)
    if engine is None:
        available_engines = ", ".join(sorted(BUILD_ENGINES))
        raise ImagerBuildError(
            f"Unsupported build engine '{build_engine}'. Available engines: {available_engines}."
        )
    selected_profile = engine.profile(profile)
    normalized_profile_metadata = _coerce_profile_metadata(profile_metadata)
    profile_manifest = _build_profile_manifest(
        build_profile=selected_profile,
        profile_metadata=normalized_profile_metadata,
    )
    recovery_ssh_access = _normalize_recovery_ssh_access(
        recovery_ssh_user=recovery_ssh_user,
        recovery_authorized_keys=recovery_authorized_keys,
    )
    if skip_recovery_ssh and recovery_ssh_access and recovery_ssh_access.enabled:
        raise ImagerBuildError(
            "skip_recovery_ssh cannot be combined with recovery SSH keys."
        )
    if (
        customize
        and not skip_recovery_ssh
        and not (recovery_ssh_access and recovery_ssh_access.enabled)
    ):
        raise ImagerBuildError(
            "Recovery SSH is required for customized image builds. "
            "Provide recovery authorized keys or explicitly skip recovery SSH."
        )
    if recovery_ssh_access and recovery_ssh_access.enabled and not customize:
        raise ImagerBuildError(
            "Recovery SSH access requires image customization. Remove --skip-customize or omit recovery key options."
        )
    if not customize:
        bundle_suite = False
        if reserve_node:
            raise ImagerBuildError(
                "Reserved node images require image customization. Remove --skip-customize or omit --reserve."
            )
        if copy_all_host_networks or host_network_names or copy_parent_networks:
            raise ImagerBuildError(
                "Host network profile copying requires image customization."
            )
        if initial_profile_path is not None:
            raise ImagerBuildError("An initial profile requires image customization.")
        if connect_auth_key_path is not None:
            raise ImagerBuildError(
                "Raspberry Pi Connect auth key injection requires image customization."
            )

    resolved_initial_profile_path = initial_profile_path
    initial_profile = None
    if resolved_initial_profile_path is not None:
        resolved_initial_profile_path = (
            resolved_initial_profile_path.expanduser().resolve()
        )
        if not resolved_initial_profile_path.is_file():
            raise ImagerBuildError(
                f"Initial profile does not exist or is not a file: {resolved_initial_profile_path}"
            )
        initial_profile = _load_initial_profile_build_settings(
            resolved_initial_profile_path
        )
    resolved_connect_auth_key_path = connect_auth_key_path
    if resolved_connect_auth_key_path is not None:
        if skip_connect_bootstrap:
            raise ImagerBuildError(
                "Raspberry Pi Connect auth key injection cannot be combined with "
                "disabled Connect bootstrap."
            )
        resolved_connect_auth_key_path = (
            resolved_connect_auth_key_path.expanduser().resolve()
        )
        _connect_auth_key_from_file(resolved_connect_auth_key_path)
        connect_bootstrap_enabled = True

    resolved_suite_source_path = suite_source_path
    if customize and bundle_suite and resolved_suite_source_path is None:
        resolved_suite_source_path = Path(settings.BASE_DIR)
    resolved_host_network_names = list(host_network_names or ())
    if initial_profile is not None:
        if initial_profile.node_number is not None:
            if (
                reserve_number is not None
                and reserve_number != initial_profile.node_number
            ):
                raise ImagerBuildError(
                    "Initial profile node.number conflicts with --reserve-number."
                )
            reserve_number = initial_profile.node_number
            reserve_node = True
        for profile_name in initial_profile.host_network_names:
            if profile_name not in resolved_host_network_names:
                resolved_host_network_names.append(profile_name)
    if reserve_node and not skip_connect_bootstrap:
        connect_bootstrap_enabled = True
    if copy_parent_networks and not copy_all_host_networks:
        for profile_name in active_parent_network_names():
            if profile_name not in resolved_host_network_names:
                resolved_host_network_names.append(profile_name)
    network_profiles = select_host_network_profiles(
        profile_dir=host_network_profile_dir,
        names=resolved_host_network_names,
        copy_all=copy_all_host_networks,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    output_filename = f"{name}-{TARGET_RPI4B}.img"
    output_path = output_dir / output_filename
    with TemporaryDirectory(dir=output_dir) as temporary_directory:
        source_path = _resolve_base_image(base_image_uri, Path(temporary_directory))
        if source_path.resolve() == output_path.resolve():
            raise ImagerBuildError(
                "Base image path must differ from output artifact path."
            )
        shutil.copyfile(source_path, output_path)

    image_size_adjustment = _ensure_image_minimum_size(
        output_path,
        minimum_size_bytes=effective_minimum_image_size_bytes,
    )
    try:
        reservation = (
            plan_image_reservation(
                hostname_prefix=reserve_hostname_prefix,
                number=reserve_number,
                role_name=reserve_role,
                next_number_base_url=next_number_base_url,
                downstream_registration_base_url=downstream_registration_base_url,
                claim_token=reservation_claim_token,
            )
            if reserve_node
            else None
        )
    except (RemoteReservationError, ValueError) as exc:
        raise ImagerBuildError(str(exc)) from exc

    customization_result = ImageCustomizationResult()
    recovery_ap_psk = ""
    recovery_ap_psk_sidecar: Path | None = None
    if customize:
        recovery_ap_psk = _generate_recovery_ap_psk()
        raw_customization_result = _customize_image(
            output_path,
            git_url=git_url,
            recovery_ssh_access=recovery_ssh_access,
            suite_source_path=resolved_suite_source_path if bundle_suite else None,
            initial_profile_path=resolved_initial_profile_path,
            connect_auth_key_path=resolved_connect_auth_key_path,
            network_profiles=network_profiles,
            reservation=reservation,
            connect_bootstrap_enabled=connect_bootstrap_enabled,
            recovery_ap_psk=recovery_ap_psk,
            initial_profile_requires_nftables=bool(
                initial_profile and initial_profile.redirect
            ),
        )
        if isinstance(raw_customization_result, ImageCustomizationResult):
            customization_result = raw_customization_result
        if customization_result.recovery_ap_psk_path:
            recovery_ap_psk_sidecar = _write_recovery_ap_psk_sidecar(
                output_path, recovery_ap_psk
            )

    sha256 = _sha256_for_file(output_path)
    size_bytes = output_path.stat().st_size
    download_uri = _build_download_uri(download_base_uri, output_filename)

    with transaction.atomic():
        try:
            reservation_commit = (
                commit_image_reservation(reservation)
                if reservation is not None
                else None
            )
        except ValueError as exc:
            raise ImagerBuildError(str(exc)) from exc
        reservation_payload = (
            reservation_commit.metadata() if reservation_commit is not None else None
        )
        RaspberryPiImageArtifact.objects.update_or_create(
            name=name,
            defaults={
                "target": TARGET_RPI4B,
                "base_image_uri": base_image_uri,
                "output_filename": output_filename,
                "output_path": str(output_path),
                "sha256": sha256,
                "size_bytes": size_bytes,
                "download_uri": download_uri,
                "metadata": {
                    "build_engine": build_engine,
                    "build_profile": profile,
                    "profile_manifest": profile_manifest,
                    "bootstrap_service": "arthexis-bootstrap.service",
                    "bootstrap_script": "/usr/local/bin/arthexis-bootstrap.sh",
                    "first_boot_script": "firstrun.sh",
                    "git_url": git_url,
                    "suite_bundle": _suite_bundle_metadata(
                        customization_result.suite_bundle
                    ),
                    "host_network_profiles": _network_profiles_metadata(
                        customization_result.network_profiles
                    ),
                    "reserved_node": _reservation_metadata(reservation_payload),
                    "image_size": _image_size_metadata(image_size_adjustment),
                    "recovery_ap": {
                        "enabled_for_roles": ["Satellite", "Control"],
                        "ssid_template": "arthexis-<reserved node number>",
                        "psk_provisioned": bool(
                            customization_result.recovery_ap_psk_path
                        ),
                        "psk_path": customization_result.recovery_ap_psk_path,
                        "psk_sidecar": (
                            str(recovery_ap_psk_sidecar)
                            if recovery_ap_psk_sidecar is not None
                            else ""
                        ),
                    },
                    "recovery_ssh": {
                        "enabled": bool(
                            customize
                            and recovery_ssh_access
                            and recovery_ssh_access.enabled
                        ),
                        "user": (
                            recovery_ssh_access.username if recovery_ssh_access else ""
                        ),
                        "authorized_key_count": (
                            len(recovery_ssh_access.authorized_keys)
                            if recovery_ssh_access
                            else 0
                        ),
                        "explicitly_skipped": bool(customize and skip_recovery_ssh),
                    },
                    "artifact_storage": {
                        "backend": normalized_storage_backend,
                        "options": _sanitize_storage_options(
                            normalized_storage_options
                        ),
                        "external_upload_configured": normalized_storage_backend
                        != STORAGE_BACKEND_LOCAL,
                        "external_upload_implemented": False,
                    },
                },
                "build_engine": build_engine,
                "build_profile": profile,
            },
        )

    return BuildResult(
        name=name,
        target=TARGET_RPI4B,
        base_image_uri=base_image_uri,
        output_path=output_path,
        sha256=sha256,
        size_bytes=size_bytes,
        download_uri=download_uri,
        build_engine=build_engine,
        build_profile=profile,
        profile_manifest=profile_manifest,
        storage_backend=normalized_storage_backend,
        storage_options=normalized_storage_options,
        reservation=reservation_payload,
    )
