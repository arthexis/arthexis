#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/helpers/logging.sh
. "$BASE_DIR/scripts/helpers/logging.sh"
arthexis_resolve_log_dir "$BASE_DIR" LOG_DIR || exit 1
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1

ROLE=""
USERNAME="arthe"
PASSWORD=""
DEVICE_LAYER_REQUEST="pi4"
DEVICE_LAYER_CANONICAL=""
DEVICE_LAYER_ALIAS=""
PASSWORD_PROVIDED=false
HOSTNAME=""

usage() {
  cat <<USAGE
Usage: $0 (--control | --satellite) --hostname NAME [--user NAME] [--password PASS] [--device LAYER]

Options:
  --control           Build an image configured as a Control node.
  --satellite         Build an image configured as a Satellite node.
  --hostname NAME     Assign NAME as the system hostname (required).
  --user NAME         Provision the named Linux account (default: arthe).
  --password PASS     Use PASS as the account password (otherwise prompt).
  --device LAYER      Target rpi-image-gen device layer alias (default: pi4; resolves canonical names automatically).
  -h, --help          Show this help message.
USAGE
}

error() {
  echo "Error: $*" >&2
  exit 1
}

validate_username() {
  local name="$1"
  if [[ ! $name =~ ^[a-z_][a-z0-9_-]*$ ]]; then
    error "Username must match ^[a-z_][a-z0-9_-]*$"
  fi
}

validate_hostname() {
  local name="$1"
  local length=${#name}
  if (( length < 1 || length > 63 )); then
    error "Hostname must be 1-63 characters"
  fi
  if [[ ! $name =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]]; then
    error "Hostname must match ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --control)
      ROLE="control"
      ;;
    --satellite)
      ROLE="satellite"
      ;;
    --user)
      [[ $# -ge 2 ]] || error "--user requires a value"
      USERNAME="$2"
      shift
      ;;
    --password)
      [[ $# -ge 2 ]] || error "--password requires a value"
      PASSWORD="$2"
      PASSWORD_PROVIDED=true
      shift
      ;;
    --device)
      [[ $# -ge 2 ]] || error "--device requires a value"
      DEVICE_LAYER_REQUEST="$2"
      shift
      ;;
    --hostname)
      [[ $# -ge 2 ]] || error "--hostname requires a value"
      HOSTNAME="${2,,}"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      error "Unknown option: $1"
      ;;
  esac
  shift
done

[[ -n "$ROLE" ]] || error "Select either --control or --satellite"
[[ -n "$HOSTNAME" ]] || error "Hostname is required (--hostname)"
validate_hostname "$HOSTNAME"
validate_username "$USERNAME"

if [[ "$PASSWORD_PROVIDED" = false ]]; then
  while true; do
    read -rsp "Password for user '$USERNAME': " p1
    echo
    read -rsp "Confirm password: " p2
    echo
    if [[ -n "$p1" && "$p1" == "$p2" ]]; then
      PASSWORD="$p1"
      break
    else
      echo "Passwords do not match or are empty. Please try again." >&2
    fi
  done
else
  [[ -n "$PASSWORD" ]] || error "Password cannot be empty"
fi

if [[ ${#PASSWORD} -lt 8 || ${#PASSWORD} -gt 63 ]]; then
  echo "Warning: Wi-Fi passphrase should be 8-63 characters; provided length is ${#PASSWORD}" >&2
fi

TIMESTAMP="$(date +%Y%m%d%H%M%S)"
WORK_ROOT="$BASE_DIR/build/rpi-image-gen"
RUN_DIR="$WORK_ROOT/run-$TIMESTAMP"
RPI_DIR="$WORK_ROOT/rpi-image-gen"
SOURCE_DIR="$RUN_DIR/source"
CONFIG_DIR="$SOURCE_DIR/config"
LAYER_DIR="$SOURCE_DIR/layer"
OUTPUT_DIR="$BASE_DIR/build/images"
BUILD_DIR="$RUN_DIR/build"
mkdir -p "$RUN_DIR" "$SOURCE_DIR" "$CONFIG_DIR" "$LAYER_DIR" "$OUTPUT_DIR" "$BUILD_DIR"

cleanup() {
  if [[ -n "${PASSWORD_FILE:-}" && -f "$PASSWORD_FILE" ]]; then
    shred -u "$PASSWORD_FILE" >/dev/null 2>&1 || rm -f "$PASSWORD_FILE"
  fi
}
trap cleanup EXIT

RPI_TARBALL="$WORK_ROOT/rpi-image-gen.tar.gz"
RESOLVED_DEVICE_ALIAS=""

find_device_layer_metadata() {
  local layer_name="$1"
  local base_dir="$RPI_DIR/device"

  if [[ -d "$base_dir/$layer_name" ]]; then
    for candidate in device layer; do
      if [[ -f "$base_dir/$layer_name/${candidate}.yaml" ]]; then
        printf '%s\n' "$base_dir/$layer_name/${candidate}.yaml"
        return 0
      fi
    done

    local yaml_files=()
    shopt -s nullglob
    yaml_files=("$base_dir/$layer_name/"*.yaml)
    shopt -u nullglob
    if (( ${#yaml_files[@]} > 0 )); then
      printf '%s\n' "${yaml_files[0]}"
      return 0
    fi
  fi

  if [[ -f "$base_dir/$layer_name.yaml" ]]; then
    printf '%s\n' "$base_dir/$layer_name.yaml"
    return 0
  fi

  return 1
}

read_canonical_device_layer_name() {
  local metadata_file="$1"
  awk '
    /^# *X-Env-Layer-Name:/ {
      sub(/^# *X-Env-Layer-Name:[[:space:]]*/, "")
      gsub(/\r/, "")
      name=$0
      sub(/^[[:space:]]+/, "", name)
      sub(/[[:space:]]+$/, "", name)
      print name
      exit
    }
  ' "$metadata_file"
}

resolve_device_layer() {
  local requested_layer="$1"
  local base_dir="$RPI_DIR/device"
  local metadata_file=""
  local canonical_layer=""
  local alias_layer="$requested_layer"

  if metadata_file=$(find_device_layer_metadata "$requested_layer" 2>/dev/null); then
    canonical_layer="$(read_canonical_device_layer_name "$metadata_file")"
    if [[ -z "$canonical_layer" ]]; then
      canonical_layer="$alias_layer"
    fi
    RESOLVED_DEVICE_ALIAS="$alias_layer"
    printf '%s\n' "$canonical_layer"
    return 0
  fi

  shopt -s nullglob
  local candidate_dir
  for candidate_dir in "$base_dir"/*; do
    [[ -d "$candidate_dir" ]] || continue
    local candidate_alias
    candidate_alias="$(basename "$candidate_dir")"
    metadata_file=$(find_device_layer_metadata "$candidate_alias" 2>/dev/null) || continue
    canonical_layer="$(read_canonical_device_layer_name "$metadata_file")"
    if [[ -n "$canonical_layer" && "$canonical_layer" == "$requested_layer" ]]; then
      RESOLVED_DEVICE_ALIAS="$candidate_alias"
      printf '%s\n' "$canonical_layer"
      shopt -u nullglob
      return 0
    fi
  done
  shopt -u nullglob

  return 1
}

fetch_rpi_image_gen() {
  echo "Fetching rpi-image-gen..."
  rm -f "$RPI_TARBALL"
  curl -L "https://github.com/raspberrypi/rpi-image-gen/archive/refs/heads/master.tar.gz" -o "$RPI_TARBALL"
  rm -rf "$WORK_ROOT/rpi-image-gen-master"
  tar -xzf "$RPI_TARBALL" -C "$WORK_ROOT"
  mv "$WORK_ROOT/rpi-image-gen-master" "$RPI_DIR"
}

if [[ ! -d "$RPI_DIR" ]]; then
  fetch_rpi_image_gen
fi

if DEVICE_LAYER_CANONICAL="$(resolve_device_layer "$DEVICE_LAYER_REQUEST" 2>/dev/null)"; then
  :
else
  echo "Refreshing cached rpi-image-gen sources (missing device layer '$DEVICE_LAYER_REQUEST')."
  rm -rf "$RPI_DIR"
  fetch_rpi_image_gen
  DEVICE_LAYER_CANONICAL="$(resolve_device_layer "$DEVICE_LAYER_REQUEST")" || \
    error "Device layer '$DEVICE_LAYER_REQUEST' not found in rpi-image-gen repository at $RPI_DIR."
fi

DEVICE_LAYER_ALIAS="${RESOLVED_DEVICE_ALIAS:-$DEVICE_LAYER_REQUEST}"

if [[ "$DEVICE_LAYER_ALIAS" != "$DEVICE_LAYER_REQUEST" || "$DEVICE_LAYER_CANONICAL" != "$DEVICE_LAYER_REQUEST" ]]; then
  echo "Resolved device layer '$DEVICE_LAYER_REQUEST' to alias '$DEVICE_LAYER_ALIAS' (canonical '$DEVICE_LAYER_CANONICAL')."
else
  echo "Using device layer '$DEVICE_LAYER_CANONICAL'."
fi

case "$DEVICE_LAYER_CANONICAL" in
  rpi5|rpi-cm5)
    error "Device layer '$DEVICE_LAYER_CANONICAL' targets hardware newer than the Raspberry Pi 4B, which is not supported. Use --device pi4 or an earlier model."
    ;;
esac

ensure_dependencies() {
  local dependencies_sh="$RPI_DIR/lib/dependencies.sh"
  local install_script="$RPI_DIR/install_deps.sh"
  local depends_file="$RPI_DIR/depends"

  if [[ ! -r "$dependencies_sh" || ! -f "$depends_file" ]]; then
    return
  fi

  local check_output=""
  local check_status=0

  set +e
  check_output=$( ( source "$dependencies_sh"; dependencies_check "$depends_file" ) 2>&1 )
  check_status=$?
  set -e

  if (( check_status == 0 )); then
    return
  fi

  if [[ -n "$check_output" ]]; then
    printf '%s\n' "$check_output"
  fi

  if (( EUID == 0 )); then
    echo "Missing dependencies detected. Attempting automatic installation..."

    if ! command -v apt >/dev/null 2>&1; then
      echo "Automatic dependency installation requires apt but it was not found. Install the packages manually."
      exit 1
    fi

    if [[ ! -f "$install_script" ]]; then
      echo "Automatic dependency installation script not found at $install_script. Install the packages manually."
      exit 1
    fi

    if [[ ! -x "$install_script" ]]; then
      chmod +x "$install_script" 2>/dev/null || true
    fi

    set +e
    DEBIAN_FRONTEND=${DEBIAN_FRONTEND:-noninteractive} "$install_script"
    local install_status=$?
    set -e
    if (( install_status != 0 )); then
      local failure_output=""
      set +e
      failure_output=$( ( source "$dependencies_sh"; dependencies_check "$depends_file" ) 2>&1 )
      set -e
      if [[ -n "$failure_output" ]]; then
        printf '%s\n' "$failure_output"
      fi
      echo "Automatic dependency installation failed with exit code $install_status. Please review the output above and install the packages manually."
      exit $install_status
    fi

    local recheck_output=""
    local recheck_status=0
    set +e
    recheck_output=$( ( source "$dependencies_sh"; dependencies_check "$depends_file" ) 2>&1 )
    recheck_status=$?
    set -e

    if (( recheck_status == 0 )); then
      echo "Dependencies installed successfully."
      return
    fi

    if [[ -n "$recheck_output" ]]; then
      printf '%s\n' "$recheck_output"
    fi
    echo "Dependencies remain missing after the automatic installation attempt. Please install them manually."
    exit 1
  fi

  echo "Run this script again with sudo to install the missing dependencies automatically, or install them manually using the instructions above."
  exit 1
}

ensure_dependencies

PASSWORD_FILE="$RUN_DIR/.user-password"
chmod 700 "$RUN_DIR"
printf '%s\n' "$PASSWORD" > "$PASSWORD_FILE"
chmod 600 "$PASSWORD_FILE"

generate_uuid() {
  if command -v uuidgen >/dev/null 2>&1; then
    uuidgen
  elif command -v python3 >/dev/null 2>&1; then
    python3 - <<'PY'
import uuid
print(uuid.uuid4())
PY
  else
    error "Neither uuidgen nor python3 is available to generate UUIDs. Install uuid-runtime or Python 3."
  fi
}

AP_UUID="$(generate_uuid)"
ETH_UUID="$(generate_uuid)"

IMAGE_NAME="arthexis-${ROLE}-${HOSTNAME}"
CONFIG_FILE="$CONFIG_DIR/${IMAGE_NAME}.yaml"
cat > "$CONFIG_FILE" <<CFG
device:
  layer: $DEVICE_LAYER_CANONICAL

image:
  layer: image-rpios
  boot_part_size: 200%
  root_part_size: 300%
  name: $IMAGE_NAME

layer:
  base: bookworm-minbase
  arthexis: arthexis-node
CFG

LAYER_FILE="$LAYER_DIR/arthexis-node.yaml"
cat > "$LAYER_FILE" <<'LAYER'
# METABEGIN
# X-Env-Layer-Name: arthexis-node
# X-Env-Layer-Category: arthexis
# X-Env-Layer-Desc: Provision Arthexis control or satellite nodes.
# X-Env-Layer-Version: 1.0.0
# X-Env-Layer-Requires: bookworm-minbase
# X-Env-VarPrefix: arthexis
# X-Env-Var-username-Desc: Service account name
# X-Env-Var-username-Required: y
# X-Env-Var-username-Valid: string
# X-Env-Var-password_file-Desc: Path to a file containing the account password
# X-Env-Var-password_file-Required: y
# X-Env-Var-password_file-Valid: path
# X-Env-Var-role-Desc: Target node role (control or satellite)
# X-Env-Var-role-Required: y
# X-Env-Var-role-Valid: string
# X-Env-Var-hostname-Desc: Hostname to assign to the device
# X-Env-Var-hostname-Required: y
# X-Env-Var-hostname-Valid: string
# X-Env-Var-repo_src-Desc: Host path to the Arthexis repository
# X-Env-Var-repo_src-Required: y
# X-Env-Var-repo_src-Valid: path
# X-Env-Var-wifi_uuid-Desc: UUID for the gelectriic access point connection
# X-Env-Var-wifi_uuid-Required: y
# X-Env-Var-wifi_uuid-Valid: string
# X-Env-Var-eth_uuid-Desc: UUID for the eth0 shared connection
# X-Env-Var-eth_uuid-Required: y
# X-Env-Var-eth_uuid-Valid: string
# METAEND
---
mmdebstrap:
  packages:
    - sudo
    - git
    - python3
    - python3-venv
    - python3-pip
    - build-essential
    - nginx
    - redis-server
    - network-manager
    - dnsmasq
    - iptables
    - openssh-server
    - ca-certificates
    - curl
  customize-hooks:
    - |-
        set -euo pipefail
        install -d -m 755 "$1/usr/local/bin"
        cat > "$1/usr/local/bin/systemctl" <<'STUB'
#!/bin/bash
set -euo pipefail
LOG_DIR=/var/log/arthexis-image
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/systemctl.log"
cmd="${1:-}"
if [[ -z "$cmd" ]]; then
  exit 0
fi
shift || true
normalize_unit() {
  local unit="$1"
  if [[ "$unit" != *.* ]]; then
    unit="${unit}.service"
  fi
  echo "$unit"
}
find_unit_source() {
  local unit="$1"
  local candidate
  for candidate in \
    "/etc/systemd/system/$unit" \
    "/lib/systemd/system/$unit" \
    "/usr/lib/systemd/system/$unit"; do
    if [[ -e "$candidate" ]]; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  return 1
}
case "$cmd" in
  enable)
    mkdir -p /etc/systemd/system/multi-user.target.wants
    status=0
    for unit in "$@"; do
      unit="$(normalize_unit "$unit")"
      dest="/etc/systemd/system/multi-user.target.wants/$unit"
      if src="$(find_unit_source "$unit")"; then
        if [[ "$src" == /etc/systemd/system/* ]]; then
          ln -sf "../$(basename "$src")" "$dest"
        else
          ln -sf "$src" "$dest"
        fi
        printf 'enable %s %s\n' "$unit" "$(date --iso-8601=seconds 2>/dev/null || date)" >> "$LOG_FILE"
      else
        printf 'enable-missing %s %s\n' "$unit" "$(date --iso-8601=seconds 2>/dev/null || date)" >> "$LOG_FILE"
      fi
    done
    exit $status
    ;;
  disable)
    for unit in "$@"; do
      unit="$(normalize_unit "$unit")"
      rm -f "/etc/systemd/system/multi-user.target.wants/$unit"
      printf 'disable %s %s\n' "$unit" "$(date --iso-8601=seconds 2>/dev/null || date)" >> "$LOG_FILE"
    done
    exit 0
    ;;
  daemon-reload)
    printf 'daemon-reload %s\n' "$(date --iso-8601=seconds 2>/dev/null || date)" >> "$LOG_FILE"
    exit 0
    ;;
  start|restart|stop)
    printf '%s %s %s\n' "$cmd" "$*" "$(date --iso-8601=seconds 2>/dev/null || date)" >> "$LOG_FILE"
    exit 0
    ;;
  list-unit-files)
    find /etc/systemd/system -maxdepth 1 -type f -name '*.service' -printf '%f enabled\n'
    exit 0
    ;;
  *)
    printf 'noop %s %s\n' "$cmd" "$(date --iso-8601=seconds 2>/dev/null || date)" >> "$LOG_FILE"
    exit 0
    ;;
esac
STUB
        chmod 755 "$1/usr/local/bin/systemctl"
    - |-
        set -euo pipefail
        cat > "$1/etc/hostname" <<EOF
$IGconf_arthexis_hostname
EOF
        cat > "$1/etc/hosts" <<EOF
127.0.0.1   localhost
127.0.1.1   $IGconf_arthexis_hostname

::1         localhost ip6-localhost ip6-loopback
ff02::1     ip6-allnodes
ff02::2     ip6-allrouters
EOF
    - |-
        set -euo pipefail
        username="$IGconf_arthexis_username"
        chroot "$1" /usr/sbin/groupadd -f sudo
        if ! chroot "$1" id -u "$username" >/dev/null 2>&1; then
          chroot "$1" /usr/sbin/useradd -m -s /bin/bash "$username"
        fi
        chroot "$1" /usr/sbin/usermod -aG sudo "$username"
    - |-
        set -euo pipefail
        install -d -m 700 "$1/opt/arthexis-image"
        install -m 600 "$IGconf_arthexis_password_file" "$1/opt/arthexis-image/user-password"
    - |-
        set -euo pipefail
        chroot "$1" /bin/bash -lc "pass=\$(head -n1 /opt/arthexis-image/user-password); printf '%s:%s\n' '$IGconf_arthexis_username' \"\$pass\" | chpasswd"
    - |-
        set -euo pipefail
        repo_src="$IGconf_arthexis_repo_src"
        repo_dest="$1/home/$IGconf_arthexis_username/arthexis"
        mkdir -p "$repo_dest"
        tar -C "$repo_src" \
          --exclude-vcs \
          --exclude='.venv' \
          --exclude='build' \
          --exclude='dist' \
          --exclude='logs' \
          --exclude='backups' \
          --exclude='*.pyc' \
          --exclude='__pycache__' \
          --exclude='db.sqlite3*' \
          --exclude='*.log' \
          --exclude='*.img' \
          -cf - . | tar -C "$repo_dest" -xf -
        chroot "$1" chown -R "$IGconf_arthexis_username:$IGconf_arthexis_username" "/home/$IGconf_arthexis_username"
    - |-
        set -euo pipefail
        cat > "$1/opt/arthexis-image/provision-user.sh" <<'USR'
#!/bin/bash
set -euo pipefail
PASS_FILE="/opt/arthexis-image/user-password"
if [ ! -s "$PASS_FILE" ]; then
  echo "Password file missing" >&2
  exit 1
fi
PASS=$(head -n1 "$PASS_FILE")
cd "__ARTHEXIS_HOME__/arthexis"
printf '%s\n' "$PASS" | sudo -S -v
./install.sh __ARTHEXIS_INSTALL_ARGS__
USR
        chmod 700 "$1/opt/arthexis-image/provision-user.sh"
    - |-
        set -euo pipefail
        cat > "$1/opt/arthexis-image/provision-root.sh" <<'ROOT'
#!/bin/bash
set -euo pipefail
PASS_FILE="/opt/arthexis-image/user-password"
if [ ! -s "$PASS_FILE" ]; then
  echo "Password file missing" >&2
  exit 1
fi
WIFI_PASS=$(head -n1 "$PASS_FILE")
NM_DIR="/etc/NetworkManager/system-connections"
AP_UUID="__ARTHEXIS_AP_UUID__"
ETH_UUID="__ARTHEXIS_ETH_UUID__"
install -d -m 700 "$NM_DIR"
NM_DIR="$NM_DIR" AP_UUID="$AP_UUID" ETH_UUID="$ETH_UUID" WIFI_PASS="$WIFI_PASS" python3 - <<PY
import os, textwrap, pathlib
nm_dir = os.environ["NM_DIR"]
wifi_uuid = os.environ["AP_UUID"]
eth_uuid = os.environ["ETH_UUID"]
wifi_pass = os.environ["WIFI_PASS"]
wifi = textwrap.dedent(f"""\
[connection]
id=gelectriic-ap
uuid={wifi_uuid}
type=wifi
interface-name=wlan0
autoconnect=true
autoconnect-priority=0

[wifi]
mode=ap
band=bg
ssid=gelectriic-ap

[wifi-security]
key-mgmt=wpa-psk
psk={wifi_pass}

[ipv4]
method=shared
addresses1=10.42.0.1/16
never-default=true

[ipv6]
method=ignore
never-default=true
""")
eth = textwrap.dedent(f"""\
[connection]
id=eth0-shared
uuid={eth_uuid}
type=ethernet
interface-name=eth0
autoconnect=true

[ipv4]
method=shared
addresses1=192.168.129.10/16
never-default=true
route-metric=10000

[ipv6]
method=ignore
never-default=true
""")
pathlib.Path(nm_dir).mkdir(parents=True, exist_ok=True)
wifi_path = pathlib.Path(nm_dir) / "gelectriic-ap.nmconnection"
eth_path = pathlib.Path(nm_dir) / "eth0-shared.nmconnection"
wifi_path.write_text(wifi)
eth_path.write_text(eth)
os.chmod(wifi_path, 0o600)
os.chmod(eth_path, 0o600)
PY
systemctl enable NetworkManager
systemctl enable ssh
if grep -Eq '^[[:space:]]*PasswordAuthentication[[:space:]]+no' /etc/ssh/sshd_config; then
  sed -i 's/^[[:space:]]*PasswordAuthentication[[:space:]]\+no/PasswordAuthentication yes/' /etc/ssh/sshd_config
fi
if ! grep -Eq '^[[:space:]]*PasswordAuthentication[[:space:]]+yes' /etc/ssh/sshd_config; then
  printf '\nPasswordAuthentication yes\n' >> /etc/ssh/sshd_config
fi
install -d -m 755 "$(dirname '__ARTHEXIS_FIRSTBOOT_FLAG__')"
cat > /usr/local/sbin/arthexis-firstboot.sh <<'BOOT'
#!/bin/bash
set -euo pipefail
FLAG="__ARTHEXIS_FIRSTBOOT_FLAG__"
if [ -f "$FLAG" ]; then
  exit 0
fi
mkdir -p "$(dirname "$FLAG")"
ARTHEXIS_HOME="/home/__ARTHEXIS_USER__/arthexis"
SERVICE_NAME="arthexis"
if [ -r "$ARTHEXIS_HOME/locks/service.lck" ]; then
  SERVICE_NAME="$(head -n1 "$ARTHEXIS_HOME/locks/service.lck")"
fi
declare -a units=("redis-server" "nginx" "NetworkManager" "ssh" "$SERVICE_NAME")
if [ -f "$ARTHEXIS_HOME/locks/celery.lck" ]; then
  units+=("celery-$SERVICE_NAME" "celery-beat-$SERVICE_NAME")
fi
if [ -f "$ARTHEXIS_HOME/locks/datasette.lck" ]; then
  units+=("datasette-$SERVICE_NAME")
fi
if [ -f "$ARTHEXIS_HOME/locks/lcd_screen.lck" ]; then
  units+=("lcd-$SERVICE_NAME")
fi
systemctl daemon-reload
for unit in "${units[@]}"; do
  if systemctl list-unit-files "$unit.service" >/dev/null 2>&1; then
    systemctl enable "$unit" || true
    systemctl start "$unit" || true
  fi
done
touch "$FLAG"
BOOT
chmod 700 /usr/local/sbin/arthexis-firstboot.sh
cat > /etc/systemd/system/arthexis-firstboot.service <<'UNIT'
[Unit]
Description=Initialize Arthexis services on first boot
After=network-online.target
Wants=network-online.target
ConditionPathExists=!__ARTHEXIS_FIRSTBOOT_FLAG__

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/arthexis-firstboot.sh

[Install]
WantedBy=multi-user.target
UNIT
mkdir -p /etc/systemd/system/multi-user.target.wants
ln -sf ../arthexis-firstboot.service /etc/systemd/system/multi-user.target.wants/arthexis-firstboot.service
chown -R "__ARTHEXIS_USER__":"__ARTHEXIS_USER__" "/home/__ARTHEXIS_USER__"
rm -f "$PASS_FILE"
rm -f /opt/arthexis-image/provision-user.sh /opt/arthexis-image/provision-root.sh
rmdir /opt/arthexis-image 2>/dev/null || true
rm -f /usr/local/bin/systemctl
ROOT
        chmod 700 "$1/opt/arthexis-image/provision-root.sh"
    - |-
        set -euo pipefail
        chroot "$1" /bin/bash -lc 'if command -v redis-server >/dev/null 2>&1; then redis-server --daemonize yes; fi'
    - |-
        set -euo pipefail
        chroot "$1" /bin/bash -lc "su - $IGconf_arthexis_username -c '/opt/arthexis-image/provision-user.sh'"
    - |-
        set -euo pipefail
        chroot "$1" /bin/bash -lc "/opt/arthexis-image/provision-root.sh"
    - |-
        set -euo pipefail
        chroot "$1" /bin/bash -lc 'if command -v redis-cli >/dev/null 2>&1; then redis-cli shutdown || true; fi'
LAYER

sed -i "s#__ARTHEXIS_HOME__#/home/$USERNAME#g" "$LAYER_FILE"
sed -i "s#__ARTHEXIS_INSTALL_ARGS__#--$ROLE#g" "$LAYER_FILE"
sed -i "s#__ARTHEXIS_AP_UUID__#$AP_UUID#g" "$LAYER_FILE"
sed -i "s#__ARTHEXIS_ETH_UUID__#$ETH_UUID#g" "$LAYER_FILE"
sed -i "s#__ARTHEXIS_USER__#$USERNAME#g" "$LAYER_FILE"
sed -i "s#__ARTHEXIS_FIRSTBOOT_FLAG__#/var/lib/arthexis-image/firstboot.done#g" "$LAYER_FILE"

RPI_BIN="$RPI_DIR/rpi-image-gen"
[[ -x "$RPI_BIN" ]] || chmod +x "$RPI_BIN"

BUILD_CMD=("$RPI_BIN" build -S "$SOURCE_DIR" -c "$CONFIG_FILE" -B "$BUILD_DIR" -- \
  "IGconf_device_layer=$DEVICE_LAYER_CANONICAL" \
  "IGconf_arthexis_username=$USERNAME" \
  "IGconf_arthexis_password_file=$PASSWORD_FILE" \
  "IGconf_arthexis_role=$ROLE" \
  "IGconf_arthexis_hostname=$HOSTNAME" \
  "IGconf_arthexis_repo_src=$BASE_DIR" \
  "IGconf_arthexis_wifi_uuid=$AP_UUID" \
  "IGconf_arthexis_eth_uuid=$ETH_UUID")

echo "Running rpi-image-gen build..."
"${BUILD_CMD[@]}"

OUTPUT_IMAGE_PATH="$BUILD_DIR/image-${IMAGE_NAME}/${IMAGE_NAME}.img"
if [[ ! -f "$OUTPUT_IMAGE_PATH" ]]; then
  alt_path="$OUTPUT_IMAGE_PATH.xz"
  if [[ -f "$alt_path" ]]; then
    OUTPUT_IMAGE_PATH="$alt_path"
  else
    error "Generated image not found at $OUTPUT_IMAGE_PATH"
  fi
fi

if [[ "$OUTPUT_IMAGE_PATH" =~ \.xz$ ]]; then
  FINAL_IMAGE="$OUTPUT_DIR/${IMAGE_NAME}-${TIMESTAMP}.img.xz"
else
  FINAL_IMAGE="$OUTPUT_DIR/${IMAGE_NAME}-${TIMESTAMP}.img"
fi

cp "$OUTPUT_IMAGE_PATH" "$FINAL_IMAGE"
echo "Image ready: $FINAL_IMAGE"
