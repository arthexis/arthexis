#!/bin/bash

# Network setup script
# Configures eth0 with a shared static IP and sets up both the Hyperline
# client and the gelectriic access point on wlan0 using NetworkManager (nmcli).

set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/helpers/logging.sh
. "$BASE_DIR/scripts/helpers/logging.sh"
# shellcheck source=scripts/helpers/ports.sh
. "$BASE_DIR/scripts/helpers/ports.sh"
arthexis_resolve_log_dir "$BASE_DIR" LOG_DIR || exit 1
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1
cd "$BASE_DIR"
LOCK_DIR="$BASE_DIR/locks"
mkdir -p "$LOCK_DIR"

BACKEND_PORT_DEFAULT=8888
BACKEND_PORT_LOCK="$LOCK_DIR/backend_port.lck"
PORT_CHANGE_REQUESTED=false
REQUESTED_BACKEND_PORT=""
CHECK_PORT_ONLY=false
BACKEND_PORT="$BACKEND_PORT_DEFAULT"

DEFAULT_ETH0_SUBNET_PREFIX="192.168"
DEFAULT_ETH0_SUBNET_SUFFIX="129"
DEFAULT_ETH0_SUBNET_BASE="${DEFAULT_ETH0_SUBNET_PREFIX}.${DEFAULT_ETH0_SUBNET_SUFFIX}"

CONSTELLATION_HUB=""
CONSTELLATION_INTERFACE="wg-constellation"
CONSTELLATION_CONFIG="/etc/wireguard/${CONSTELLATION_INTERFACE}.conf"
CONSTELLATION_KEYS_DIR="$BASE_DIR/security/wireguard"
CONSTELLATION_PRIVATE_KEY="$CONSTELLATION_KEYS_DIR/${CONSTELLATION_INTERFACE}.key"
CONSTELLATION_PUBLIC_KEY="$CONSTELLATION_KEYS_DIR/${CONSTELLATION_INTERFACE}.pub"
CONSTELLATION_LOCK_FILE="$LOCK_DIR/constellation_ip.lck"
CONSTELLATION_DEVICE_MANIFEST="$LOCK_DIR/constellation_devices.json"
CONSTELLATION_PORT=51820

usage() {
    cat <<USAGE
Usage: $0 [--password] [--ap NAME] [--no-firewall] [--unsafe] [--interactive|-i] [--vnc] [--no-vnc] [--subnet N[/P]] [--eth0-mode MODE] [--ap-set-password] [--status] [--dhcp-server] [--dhcp-client] [--dhcp-reset]
  --password      Prompt for a new WiFi password even if one is already configured.
  --ap NAME       Set the wlan0 access point name (SSID) to NAME.
  --port PORT     Persist the backend application port across the installation.
  --constellation HUB
                  Configure the Constellation WireGuard overlay using HUB as the watchtower endpoint.
  --check-port    Report configured backend port values and exit.
  --no-firewall   Skip firewall port validation.
  --unsafe        Allow modification of the active internet connection.
  --interactive, -i  Collect user decisions for each step before executing.
  --vnc           Require validating that a VNC service is enabled.
  --no-vnc        Skip validating that a VNC service is enabled (default).
  --subnet N[/P]  Configure eth0 on the 192.168.N.0/P subnet (default: 129/16).
                  Accepts prefix lengths of 16 or 24.
                  Ignored when eth0 operates in client mode.
  --eth0-mode MODE  Set eth0 mode to shared, client, or auto (default: auto).
  --dhcp-server   Force eth0 into shared mode (DHCP server).
  --dhcp-client   Force eth0 into client mode (DHCP client).
  --dhcp-reset    Remove Arthexis-managed ethernet profiles and restore DHCP defaults.
  --status        Print the current network configuration summary and exit.
  --ap-set-password  Update the configured access point password without running other setup steps.
USAGE
}

FORCE_PASSWORD=false
SKIP_FIREWALL=false
INTERACTIVE=false
UNSAFE=false
REQUIRE_VNC=false
VNC_OPTION_SET=false
DEFAULT_AP_NAME="gelectriic-ap"
AP_NAME="$DEFAULT_AP_NAME"
AP_SPECIFIED=false
AP_NAME_LOWER=""
SKIP_AP=false
AP_IPV6_ADDRESS="fd42:0:0:42::1/64"
ETH0_SUBNET_BASE="$DEFAULT_ETH0_SUBNET_BASE"
ETH0_PREFIX=16
ETH0_MODE="auto"
ETH0_MODE_EFFECTIVE="auto"
ETH0_CONNECTION_SHARED="eth0-shared"
ETH0_CONNECTION_CLIENT="eth0-client"
ETH0_CONNECTION_NAME="$ETH0_CONNECTION_SHARED"
ETH0_SUBNET_SPECIFIED=false
AP_SET_PASSWORD=false
OTHER_OPTIONS_USED=false
STATUS_ONLY=false
DHCP_OVERRIDE=""
ETH0_MODE_SPECIFIED=false
WLAN1_DETECTED=true
ETH0_DHCP_SERVER=""
DHCP_RESET=false
NMCLI_AVAILABLE=false

if command -v nmcli >/dev/null 2>&1; then
    NMCLI_AVAILABLE=true
fi

join_by() {
    local sep="$1"
    shift
    local first=1
    local item
    for item in "$@"; do
        if (( first )); then
            printf '%s' "$item"
            first=0
        else
            printf '%s%s' "$sep" "$item"
        fi
    done
}

is_valid_port() {
    local value="$1"
    if [[ "$value" =~ ^[0-9]+$ ]]; then
        if (( value >= 1 && value <= 65535 )); then
            return 0
        fi
    fi
    return 1
}

read_backend_port_lock() {
    if [ -f "$BACKEND_PORT_LOCK" ]; then
        local value
        value="$(tr -d '\r\n[:space:]' < "$BACKEND_PORT_LOCK" 2>/dev/null || true)"
        if is_valid_port "$value"; then
            printf '%s' "$value"
            return 0
        fi
    fi
    return 1
}

detect_backend_port_from_service() {
    local service_name
    if [ ! -f "$LOCK_DIR/service.lck" ]; then
        return 1
    fi
    service_name="$(tr -d '\r\n' < "$LOCK_DIR/service.lck" 2>/dev/null || true)"
    if [[ -z "$service_name" ]]; then
        return 1
    fi

    local service_file="/etc/systemd/system/${service_name}.service"
    local exec_line=""
    if [ -f "$service_file" ]; then
        exec_line="$(grep -E '^ExecStart=' "$service_file" 2>/dev/null | head -n1)"
        if [[ -n "$exec_line" ]]; then
            exec_line="${exec_line#ExecStart=}"
        fi
    fi

    if [[ -z "$exec_line" ]] && command -v systemctl >/dev/null 2>&1; then
        exec_line="$(systemctl show -p ExecStart --value "$service_name" 2>/dev/null | head -n1 || true)"
    fi

    if [[ -z "$exec_line" ]]; then
        return 1
    fi

    local port
    port="$(printf '%s\n' "$exec_line" | sed -n 's/.*0\\.0\\.0\\.0:\([0-9]\{2,5\}\).*/\1/p')"
    if [[ -z "$port" ]]; then
        port="$(printf '%s\n' "$exec_line" | sed -n 's/.*--port[= ]\([0-9]\{2,5\}\).*/\1/p')"
    fi

    if is_valid_port "$port"; then
        printf '%s' "$port"
        return 0
    fi

    return 1
}

detect_backend_port_from_nginx() {
    local mode="internal"
    if [ -f "$LOCK_DIR/nginx_mode.lck" ]; then
        mode="$(tr '[:upper:]' '[:lower:]' < "$LOCK_DIR/nginx_mode.lck" 2>/dev/null || echo internal)"
        [[ -z "$mode" ]] && mode="internal"
    fi
    local conf="/etc/nginx/sites-enabled/arthexis.conf"
    if [ ! -f "$conf" ]; then
        conf="/etc/nginx/conf.d/arthexis-${mode}.conf"
    fi
    if [ ! -f "$conf" ]; then
        return 1
    fi
    local port
    port="$(sed -n 's/.*proxy_pass http:\/\/127\.0\.0\.1:\([0-9]\{2,5\}\).*/\1/p' "$conf" | head -n1)"
    if is_valid_port "$port"; then
        printf '%s' "$port"
        return 0
    fi
    return 1
}

detect_backend_port_from_process() {
    if ! command -v pgrep >/dev/null 2>&1; then
        return 1
    fi
    local line
    line="$(pgrep -af "manage.py runserver" 2>/dev/null | head -n1)"
    if [[ -z "$line" ]]; then
        return 1
    fi
    local port
    port="$(printf '%s\n' "$line" | sed -n 's/.*0\\.0\\.0\\.0:\([0-9]\{2,5\}\).*/\1/p')"
    if [[ -z "$port" ]]; then
        port="$(printf '%s\n' "$line" | sed -n 's/.*--port[= ]\([0-9]\{2,5\}\).*/\1/p')"
    fi
    if is_valid_port "$port"; then
        printf '%s' "$port"
        return 0
    fi
    return 1
}

normalize_constellation_target() {
    local target="$1"
    target="${target#${target%%[![:space:]]*}}"
    target="${target%${target##*[![:space:]]}}"
    if [[ -z "$target" ]]; then
        return 1
    fi
    if [[ "$target" =~ ^https?:// ]]; then
        printf '%s' "${target%/}"
    else
        printf 'https://%s' "${target%/}"
    fi
    return 0
}

constellation_mac_address() {
    if ! command -v python3 >/dev/null 2>&1; then
        return 1
    fi
    python3 - <<'PY'
import uuid
value = f"{uuid.getnode():012x}"
print(":".join(value[i:i+2] for i in range(0, 12, 2)))
PY
}

ensure_constellation_keys() {
    if ! command -v wg >/dev/null 2>&1; then
        echo "WireGuard tool 'wg' is required for Constellation configuration." >&2
        return 1
    fi
    mkdir -p "$CONSTELLATION_KEYS_DIR"
    if [[ ! -s "$CONSTELLATION_PRIVATE_KEY" ]]; then
        umask 077
        if ! wg genkey > "$CONSTELLATION_PRIVATE_KEY"; then
            echo "Failed to generate Constellation private key at $CONSTELLATION_PRIVATE_KEY." >&2
            return 1
        fi
    fi
    if [[ ! -s "$CONSTELLATION_PUBLIC_KEY" ]]; then
        umask 077
        if ! wg pubkey < "$CONSTELLATION_PRIVATE_KEY" > "$CONSTELLATION_PUBLIC_KEY"; then
            echo "Failed to derive Constellation public key at $CONSTELLATION_PUBLIC_KEY." >&2
            return 1
        fi
    fi
    return 0
}

ensure_constellation_device() {
    local name="$1"
    local alias_value="$2"

    if [[ -z "$name" ]]; then
        return 0
    fi
    if ! command -v ip >/dev/null 2>&1; then
        return 0
    fi

    if ip link show "$name" >/dev/null 2>&1; then
        if [[ -n "$alias_value" ]]; then
            ip link set dev "$name" alias "Constellation ${alias_value}" >/dev/null 2>&1 || true
        fi
        return 0
    fi

    if ip link add dev "$name" type dummy >/dev/null 2>&1; then
        ip link set dev "$name" up >/dev/null 2>&1 || true
        if [[ -n "$alias_value" ]]; then
            ip link set dev "$name" alias "Constellation ${alias_value}" >/dev/null 2>&1 || true
        fi
        echo "Provisioned Constellation device '$name'."
    else
        echo "Warning: failed to create Constellation device '$name'." >&2
    fi
}

prune_constellation_devices() {
    local desired="$1"
    declare -A keep=()
    local entry
    while IFS= read -r entry; do
        [[ -z "$entry" ]] && continue
        keep["$entry"]=1
    done <<< "$desired"

    if ! command -v ip >/dev/null 2>&1; then
        return 0
    fi

    local existing
    while IFS= read -r existing; do
        [[ -z "$existing" ]] && continue
        if [[ -z "${keep[$existing]:-}" ]]; then
            ip link delete "$existing" >/dev/null 2>&1 || true
            echo "Removed stale Constellation device '$existing'."
        fi
    done < <(ip -o link show 2>/dev/null | awk -F': ' '/^[0-9]+: gw[0-9]+(@|:)/ {split($2,a,":"); split(a[1],b,"@"); print b[0]}')
}

synchronize_constellation_devices() {
    local payload="$1"
    if [[ -z "$payload" ]]; then
        prune_constellation_devices ""
        return 0
    fi
    if [[ "$(uname -s)" != "Linux" ]]; then
        return 0
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        return 0
    fi

    local parser_output
    parser_output="$(
        PAYLOAD="$payload" python3 - "$CONSTELLATION_DEVICE_MANIFEST" <<'PY'
import json
import os
import pathlib
import sys

manifest_path = pathlib.Path(sys.argv[1])
try:
    data = json.loads(os.environ.get("PAYLOAD") or "{}")
except json.JSONDecodeError:
    data = {}

entries = []
seen = set()

def add_entry(name, alias):
    if not name:
        return
    name = name.strip()
    if not name or name in seen:
        return
    seen.add(name)
    entries.append({"name": name, "alias": (alias or "").strip()})

add_entry(data.get("constellation_device"), data.get("assigned_ip"))
hub = data.get("hub") or {}
add_entry(hub.get("constellation_device"), hub.get("constellation_ip"))
for peer in data.get("peers") or []:
    if isinstance(peer, dict):
        add_entry(peer.get("constellation_device"), peer.get("constellation_ip"))

try:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(entries, indent=2) + "\n")
except OSError:
    pass

for item in entries:
    print(f"{item['name']}|{item['alias']}")
PY
    )"
    if [[ $? -ne 0 ]]; then
        echo "Warning: failed to parse Constellation device assignments." >&2
        return 1
    fi

    local desired_list=""
    local name alias
    while IFS='|' read -r name alias; do
        [[ -z "$name" ]] && continue
        desired_list+="$name"$'\n'
        ensure_constellation_device "$name" "$alias"
    done <<< "$parser_output"

    prune_constellation_devices "$desired_list"
    return 0
}

restore_constellation_devices_from_manifest() {
    if [[ "$(uname -s)" != "Linux" ]]; then
        return 0
    fi
    if [[ ! -f "$CONSTELLATION_DEVICE_MANIFEST" ]]; then
        prune_constellation_devices ""
        return 0
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        return 0
    fi

    local parser_output
    parser_output="$(
        python3 - "$CONSTELLATION_DEVICE_MANIFEST" <<'PY'
import json
import pathlib
import sys

manifest_path = pathlib.Path(sys.argv[1])
try:
    raw = manifest_path.read_text()
except OSError:
    sys.exit(0)

try:
    data = json.loads(raw)
except json.JSONDecodeError:
    data = []

if isinstance(data, dict):
    entries = data.get("entries")
    if not isinstance(entries, list):
        entries = []
else:
    entries = data if isinstance(data, list) else []

seen = set()
for entry in entries:
    if not isinstance(entry, dict):
        continue
    name = (entry.get("name") or "").strip()
    if not name or name in seen:
        continue
    seen.add(name)
    alias = (entry.get("alias") or "").strip()
    print(f"{name}|{alias}")
PY
    )"
    local status=$?
    if [[ $status -ne 0 ]]; then
        return 0
    fi

    local desired_list=""
    local name alias
    while IFS='|' read -r name alias; do
        [[ -z "$name" ]] && continue
        desired_list+="$name"$'\n'
        ensure_constellation_device "$name" "$alias"
    done <<< "$parser_output"

    prune_constellation_devices "$desired_list"
}

apply_constellation_config() {
    python3 - "$CONSTELLATION_PRIVATE_KEY" "$CONSTELLATION_CONFIG" "$CONSTELLATION_LOCK_FILE" <<'PY'
import json
import pathlib
import sys

private_key_path = pathlib.Path(sys.argv[1])
config_path = pathlib.Path(sys.argv[2])
lock_path = pathlib.Path(sys.argv[3])

payload = sys.stdin.read()
try:
    response = json.loads(payload)
except json.JSONDecodeError as exc:
    sys.stderr.write(f"Invalid Constellation response: {exc}\n")
    sys.exit(1)

if not private_key_path.exists():
    sys.stderr.write(f"Constellation private key {private_key_path} not found.\n")
    sys.exit(1)
private_key = private_key_path.read_text().strip()
if not private_key:
    sys.stderr.write(f"Constellation private key {private_key_path} is empty.\n")
    sys.exit(1)

assigned_ip = (response.get("assigned_ip") or "").strip()
if not assigned_ip:
    sys.stderr.write("Constellation response missing assigned_ip.\n")
    sys.exit(1)

address = (response.get("address") or "").strip()
if not address:
    subnet = (response.get("subnet") or "").strip()
    prefix = "32"
    if "/" in subnet:
        prefix = subnet.split("/", 1)[1]
    address = f"{assigned_ip}/{prefix}"

hub = response.get("hub") or {}
hub_ip = (hub.get("constellation_ip") or "").strip()
is_hub = bool(hub_ip and hub_ip == assigned_ip)

lines = [
    "[Interface]",
    f"PrivateKey = {private_key}",
    f"Address = {address}",
]

listen_port = hub.get("endpoint_port")
try:
    listen_port = int(listen_port)
except (TypeError, ValueError):
    listen_port = None

dns_values = response.get("dns") or []
if dns_values:
    lines.append(f"DNS = {', '.join(dns_values)}")
if is_hub and listen_port:
    lines.append(f"ListenPort = {listen_port}")

def format_endpoint(host, port):
    host = (host or "").strip()
    if not host or port is None:
        return ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{host}:{port}"

if not is_hub:
    peer_key = (hub.get("public_key") or "").strip()
    if not peer_key:
        sys.stderr.write("Constellation response missing hub public key.\n")
        sys.exit(1)
    lines.extend(
        [
            "",
            "[Peer]",
            f"PublicKey = {peer_key}",
        ]
    )
    allowed = hub.get("allowed_ips") or []
    subnet = response.get("subnet")
    if not allowed and subnet:
        allowed = [subnet]
    if allowed:
        lines.append(f"AllowedIPs = {', '.join(allowed)}")
    endpoint = format_endpoint(hub.get("endpoint_host"), listen_port or hub.get("endpoint_port"))
    if endpoint:
        lines.append(f"Endpoint = {endpoint}")
    keepalive = hub.get("persistent_keepalive")
    try:
        keepalive = int(keepalive)
    except (TypeError, ValueError):
        keepalive = None
    if keepalive:
        lines.append(f"PersistentKeepalive = {keepalive}")
else:
    for peer in response.get("peers") or []:
        peer_key = (peer.get("public_key") or "").strip()
        peer_ip = (peer.get("constellation_ip") or "").strip()
        if not peer_key or not peer_ip:
            continue
        lines.extend([
            "",
            "[Peer]",
            f"PublicKey = {peer_key}",
            f"AllowedIPs = {', '.join(peer.get('allowed_ips') or [f'{peer_ip}/32'])}",
        ])
        endpoint = (peer.get("endpoint") or "").strip()
        if endpoint:
            lines.append(f"Endpoint = {endpoint}")
        keepalive = peer.get("persistent_keepalive")
        try:
            keepalive = int(keepalive)
        except (TypeError, ValueError):
            keepalive = None
        if keepalive:
            lines.append(f"PersistentKeepalive = {keepalive}")

config_path.parent.mkdir(parents=True, exist_ok=True)
config_path.write_text("\n".join(lines) + "\n")
lock_path.parent.mkdir(parents=True, exist_ok=True)
lock_path.write_text(f"{assigned_ip}\n")
print(assigned_ip)
PY
}

configure_constellation_wireguard() {
    local hub_target="$1"
    local normalized
    normalized=$(normalize_constellation_target "$hub_target") || {
        echo "Invalid --constellation value: $hub_target" >&2
        return 1
    }
    if ! command -v curl >/dev/null 2>&1; then
        echo "curl is required for Constellation configuration." >&2
        return 1
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        echo "python3 is required for Constellation configuration." >&2
        return 1
    fi
    if ! command -v wg >/dev/null 2>&1 || ! command -v wg-quick >/dev/null 2>&1; then
        echo "WireGuard tools 'wg' and 'wg-quick' are required." >&2
        return 1
    fi

    if ! ensure_constellation_keys; then
        return 1
    fi

    local mac
    mac=$(constellation_mac_address) || {
        echo "Unable to determine local MAC address for Constellation registration." >&2
        return 1
    }

    local hostname
    hostname="$(hostname 2>/dev/null || echo arthexis-node)"

    local payload
    payload=$(python3 - "$mac" "$CONSTELLATION_PUBLIC_KEY" "$hostname" <<'PY'
import json
import sys

mac = sys.argv[1]
public_key_path = sys.argv[2]
hostname = sys.argv[3]

with open(public_key_path, "r", encoding="utf-8") as fh:
    public_key = fh.read().strip()

print(json.dumps({
    "mac_address": mac,
    "public_key": public_key,
    "hostname": hostname,
}))
PY
) || {
        echo "Failed to build Constellation registration payload." >&2
        return 1
    }

    local url="${normalized%/}/nodes/constellation/setup/"
    local response
    response=$(curl -fsS --max-time 20 -H "Content-Type: application/json" -d "$payload" "$url") || {
        echo "Failed to contact Constellation hub at $url" >&2
        return 1
    }

    local assigned_ip
    assigned_ip=$(printf '%s\n' "$response" | apply_constellation_config) || {
        echo "Failed to apply Constellation configuration." >&2
        return 1
    }

    synchronize_constellation_devices "$response" || true

    if [[ -n "$assigned_ip" ]]; then
        printf '%s\n' "$assigned_ip" > "$CONSTELLATION_LOCK_FILE"
    fi

    if wg show "$CONSTELLATION_INTERFACE" >/dev/null 2>&1; then
        wg-quick down "$CONSTELLATION_INTERFACE" || true
    fi
    if wg-quick up "$CONSTELLATION_INTERFACE"; then
        if command -v systemctl >/dev/null 2>&1; then
            systemctl enable "wg-quick@${CONSTELLATION_INTERFACE}" >/dev/null 2>&1 || true
            systemctl restart "wg-quick@${CONSTELLATION_INTERFACE}" >/dev/null 2>&1 || true
        fi
        echo "Constellation WireGuard interface '$CONSTELLATION_INTERFACE' configured with $assigned_ip."
    else
        echo "Failed to activate WireGuard interface '$CONSTELLATION_INTERFACE'." >&2
        return 1
    fi

    return 0
}

detect_backend_port() {
    local port
    if port="$(read_backend_port_lock 2>/dev/null)"; then
        printf '%s' "$port"
        return 0
    fi
    if port="$(detect_backend_port_from_service 2>/dev/null)"; then
        printf '%s' "$port"
        return 0
    fi
    if port="$(detect_backend_port_from_nginx 2>/dev/null)"; then
        printf '%s' "$port"
        return 0
    fi
    if port="$(detect_backend_port_from_process 2>/dev/null)"; then
        printf '%s' "$port"
        return 0
    fi
    printf '%s' "$BACKEND_PORT_DEFAULT"
    return 0
}

collect_managed_site_ports() {
    local sites_conf
    sites_conf="$(managed_sites_conf_path)"
    if [ ! -f "$sites_conf" ]; then
        return 1
    fi

    declare -A seen=()
    while IFS= read -r port; do
        if is_valid_port "$port" && [[ -z "${seen[$port]:-}" ]]; then
            seen[$port]=1
            printf '%s\n' "$port"
        fi
    done < <(sed -n 's/.*proxy_pass http:\/\/127\.0\.0\.1:\([0-9]\{2,5\}\).*/\1/p' "$sites_conf")
    return 0
}

report_backend_port_sources() {
    local resolved="$1"

    echo "Backend port sources:"

    local lock_value=""
    if lock_value="$(read_backend_port_lock 2>/dev/null)"; then
        echo "  lock file ($BACKEND_PORT_LOCK): $lock_value"
    else
        echo "  lock file ($BACKEND_PORT_LOCK): not set"
    fi

    if [ -f "$LOCK_DIR/service.lck" ]; then
        local service_name
        service_name="$(tr -d '\r\n' < "$LOCK_DIR/service.lck" 2>/dev/null || true)"
        if [[ -z "$service_name" ]]; then
            echo "  systemd service: lock empty"
        elif lock_value="$(detect_backend_port_from_service 2>/dev/null)"; then
            echo "  systemd service ($service_name): $lock_value"
        else
            echo "  systemd service ($service_name): not detected"
        fi
    else
        echo "  systemd service: not installed"
    fi

    local -a nginx_configs=(
        "/etc/nginx/conf.d/arthexis-internal.conf"
        "/etc/nginx/conf.d/arthexis-public.conf"
    )
    local found_nginx=false
    local conf
    for conf in "${nginx_configs[@]}"; do
        if [ -f "$conf" ]; then
            found_nginx=true
            local port
            port="$(sed -n 's/.*proxy_pass http:\/\/127\.0\.0\.1:\([0-9]\{2,5\}\).*/\1/p' "$conf" | head -n1)"
            if is_valid_port "$port"; then
                echo "  nginx config ($conf): $port"
            else
                echo "  nginx config ($conf): not detected"
            fi
        fi
    done
    if [ "$found_nginx" = false ]; then
        echo "  nginx config: not installed"
    fi

    local managed_conf
    managed_conf="$(managed_sites_conf_path)"
    local managed_output
    managed_output="$(collect_managed_site_ports 2>/dev/null || true)"
    if [[ -n "$managed_output" ]]; then
        local -a managed_ports=()
        while IFS= read -r port; do
            [[ -n "$port" ]] && managed_ports+=("$port")
        done <<< "$managed_output"
        local joined="$(join_by ', ' "${managed_ports[@]}")"
        echo "  managed nginx sites ($managed_conf): $joined"
    else
        echo "  managed nginx sites ($managed_conf): none"
    fi

    local process_port
    if process_port="$(detect_backend_port_from_process 2>/dev/null)"; then
        echo "  running process: $process_port"
    else
        echo "  running process: not detected"
    fi

    echo "Resolved backend port: $resolved (fallback $BACKEND_PORT_DEFAULT)"
}

update_service_backend_port() {
    local new_port="$1"
    if [ ! -f "$LOCK_DIR/service.lck" ]; then
        return 1
    fi
    local service_name
    service_name="$(tr -d '\r\n' < "$LOCK_DIR/service.lck" 2>/dev/null || true)"
    if [[ -z "$service_name" ]]; then
        return 1
    fi
    local service_file="/etc/systemd/system/${service_name}.service"
    if [ ! -f "$service_file" ]; then
        return 1
    fi

    local changed=false
    if grep -Eq 'runserver 0\.0\.0\.0:[0-9]+' "$service_file"; then
        sed -i -E "s/runserver 0\.0\.0\.0:[0-9]+/runserver 0.0.0.0:${new_port}/g" "$service_file"
        changed=true
    fi
    if grep -Eq '--port[= ][0-9]+' "$service_file"; then
        sed -i -E "s/--port=([0-9]+)/--port=${new_port}/g" "$service_file"
        sed -i -E "s/--port ([0-9]+)/--port ${new_port}/g" "$service_file"
        changed=true
    fi

    if [ "$changed" = true ]; then
        if command -v systemctl >/dev/null 2>&1; then
            systemctl daemon-reload >/dev/null 2>&1 || true
        fi
        echo "Updated systemd service '$service_name' to use port $new_port."
        return 0
    fi

    return 1
}

update_nginx_backend_port() {
    local new_port="$1"
    local -a configs=()
    local mode="internal"
    if [ -f "$LOCK_DIR/nginx_mode.lck" ]; then
        mode="$(tr '[:upper:]' '[:lower:]' < "$LOCK_DIR/nginx_mode.lck" 2>/dev/null || echo internal)"
        [[ -z "$mode" ]] && mode="internal"
    fi
    configs+=("/etc/nginx/sites-enabled/arthexis.conf")
    configs+=("/etc/nginx/conf.d/arthexis-internal.conf")
    configs+=("/etc/nginx/conf.d/arthexis-public.conf")

    declare -A seen=()
    local -a unique=()
    local conf
    for conf in "${configs[@]}"; do
        if [ -f "$conf" ] && [[ -z "${seen[$conf]:-}" ]]; then
            unique+=("$conf")
            seen[$conf]=1
        fi
    done

    local changed=false
    for conf in "${unique[@]}"; do
        local tmp
        tmp="$(mktemp)"
        sed -E "s#(proxy_pass http://127\\.0\\.0\\.1:)[0-9]+#\\1${new_port}#g" "$conf" > "$tmp"
        if ! cmp -s "$tmp" "$conf"; then
            cp "$tmp" "$conf"
            echo "Updated nginx configuration '$conf' to proxy to port $new_port."
            changed=true
        fi
        rm -f "$tmp"
    done

    if [ "$changed" = true ]; then
        return 0
    fi

    return 1
}

apply_backend_port_change() {
    local new_port="$1"
    if ! is_valid_port "$new_port"; then
        echo "Error: --port expects a value between 1 and 65535." >&2
        exit 1
    fi

    local current_lock=""
    current_lock="$(read_backend_port_lock 2>/dev/null || true)"
    if [[ "$current_lock" != "$new_port" ]]; then
        echo "$new_port" > "$BACKEND_PORT_LOCK"
        chmod 644 "$BACKEND_PORT_LOCK" 2>/dev/null || true
        echo "Persisted backend port $new_port to $BACKEND_PORT_LOCK."
    fi

    local service_changed=false
    if update_service_backend_port "$new_port"; then
        service_changed=true
    fi

    local nginx_changed=false
    if update_nginx_backend_port "$new_port"; then
        nginx_changed=true
    fi

    BACKEND_PORT="$new_port"
    apply_managed_nginx_sites "$new_port" || true

    if [ "$nginx_changed" = true ]; then
        if command -v nginx >/dev/null 2>&1; then
            if nginx -t >/dev/null 2>&1; then
                systemctl reload nginx >/dev/null 2>&1 || echo "Warning: nginx reload failed" >&2
            else
                echo "Warning: nginx configuration test failed after updating backend port" >&2
            fi
        fi
    fi

    if [ "$service_changed" = true ]; then
        echo "If the systemd service is running, restart it to apply the new port." >&2
    fi
}

describe_device_status() {
    local dev="$1"
    local state=""
    local connection=""
    local method=""
    local role_message=""

    if [[ $NMCLI_AVAILABLE == true ]]; then
        state=$(nmcli -t -f DEVICE,STATE device status 2>/dev/null | awk -F: -v dev="$dev" '$1==dev {print $2; exit}' || true)
        if [[ -z "$state" ]]; then
            echo "  $dev: device not detected"
            if [[ "$dev" == "wlan1" ]]; then
                echo "Warning: device wlan1 not found; wlan0 and eth0 clients cannot reach the internet without it." >&2
            fi
            return
        fi

        connection=$(nmcli -g GENERAL.CONNECTION device show "$dev" 2>/dev/null | head -n1 | tr -d '\n')
        if [[ "$connection" == "--" ]]; then
            connection=""
        fi

        if [[ -n "$connection" ]]; then
            method=$(nmcli -g ipv4.method connection show "$connection" 2>/dev/null | head -n1 || true)
        fi

        case "$method" in
            shared)
                role_message="DHCP server (NetworkManager shared mode)"
                ;;
            auto)
                role_message="DHCP client (automatic)"
                ;;
            manual)
                role_message="Static IPv4 configuration"
                ;;
            disabled)
                role_message="IPv4 disabled"
                ;;
            "")
                if [[ "$state" == "unmanaged" ]]; then
                    role_message="Unmanaged"
                else
                    role_message="No IPv4 configuration reported"
                fi
                ;;
            *)
                role_message="IPv4 method: $method"
                ;;
        esac
    else
        if ! ip link show "$dev" >/dev/null 2>&1; then
            echo "  $dev: device not detected"
            if [[ "$dev" == "wlan1" ]]; then
                echo "Warning: device wlan1 not found; wlan0 and eth0 clients cannot reach the internet without it." >&2
            fi
            return
        fi
        state=$(ip -o link show "$dev" 2>/dev/null | awk '{print $9}' || true)
        connection="Unavailable (NetworkManager not installed)"
        role_message="Unable to determine without NetworkManager"
    fi

    local -a ipv4_list=()
    mapfile -t ipv4_list < <(ip -o -4 addr show dev "$dev" 2>/dev/null | awk '{print $4}' || true)
    local ipv4="none"
    if (( ${#ipv4_list[@]} > 0 )); then
        ipv4=$(join_by ", " "${ipv4_list[@]}")
    fi

    local -a ipv6_list=()
    mapfile -t ipv6_list < <(ip -o -6 addr show dev "$dev" scope global 2>/dev/null | awk '{print $4}' || true)
    local ipv6="none"
    if (( ${#ipv6_list[@]} > 0 )); then
        ipv6=$(join_by ", " "${ipv6_list[@]}")
    fi

    echo "  $dev:"
    echo "    State: ${state:-unknown}"
    if [[ $NMCLI_AVAILABLE == true ]]; then
        echo "    Connection: ${connection:-none}"
    else
        echo "    Connection: $connection"
    fi
    echo "    IPv4 role: $role_message"
    echo "    IPv4 addresses: $ipv4"
    echo "    IPv6 addresses: $ipv6"
}

describe_service_status() {
    local service="$1"
    local label="$2"
    if ! systemctl show "${service}.service" >/dev/null 2>&1; then
        echo "  $label: service not installed"
        return
    fi

    local active
    active=$(systemctl show -p ActiveState --value "${service}.service" 2>/dev/null || true)
    [[ -z "$active" ]] && active="unknown"

    local enabled
    enabled=$(systemctl is-enabled "${service}.service" 2>/dev/null || true)
    [[ -z "$enabled" ]] && enabled="unknown"

    echo "  $label: ${active} (enabled: ${enabled})"
}

print_network_status() {
    if ! command -v nmcli >/dev/null 2>&1; then
        echo "NetworkManager (nmcli) not available; showing systemd-networkd status summary." >&2
        if command -v ip >/dev/null 2>&1; then
            local default_iface
            default_iface=$(ip -4 route show default | awk 'NR==1 {for (i=1; i<=NF; i++) if ($i=="dev") {print $(i+1); exit}}')
            if [[ -n "$default_iface" ]]; then
                echo "  Default interface: $default_iface"
                ip -4 route show default dev "$default_iface" | sed 's/^/  /'
                ip -4 addr show dev "$default_iface" | sed 's/^/  /'
            else
                echo "  No IPv4 default route configured."
            fi
        else
            echo "  ip utility not available; unable to summarize routing." >&2
        fi
        if systemctl is-active --quiet systemd-networkd 2>/dev/null; then
            echo "  systemd-networkd: $(systemctl is-active systemd-networkd 2>/dev/null)"
        fi
        if systemctl is-active --quiet systemd-resolved 2>/dev/null; then
            echo "  systemd-resolved: $(systemctl is-active systemd-resolved 2>/dev/null)"
        fi
        return 0
    fi

    local eth0_connection=""
    local eth0_method="unknown"
    local eth0_state=""
    if [[ $NMCLI_AVAILABLE == true ]]; then
        eth0_state=$(nmcli -t -f DEVICE,STATE device status 2>/dev/null | awk -F: '$1=="eth0" {print $2; exit}' || true)
        if [[ -n "$eth0_state" ]]; then
            eth0_connection=$(nmcli -g GENERAL.CONNECTION device show eth0 2>/dev/null | head -n1 | tr -d '\n')
            if [[ "$eth0_connection" == "--" ]]; then
                eth0_connection=""
            fi
            if [[ -n "$eth0_connection" ]]; then
                eth0_method=$(nmcli -g ipv4.method connection show "$eth0_connection" 2>/dev/null | head -n1 || true)
            else
                eth0_method=""
            fi
        else
            eth0_method=""
        fi
    else
        if ip link show eth0 >/dev/null 2>&1; then
            eth0_state=$(ip -o link show eth0 2>/dev/null | awk '{print $9}' || true)
        fi
    fi

    local -a eth0_ipv4_list=()
    mapfile -t eth0_ipv4_list < <(ip -o -4 addr show dev eth0 2>/dev/null | awk '{print $4}' || true)
    local eth0_primary_ip=""
    if (( ${#eth0_ipv4_list[@]} > 0 )); then
        eth0_primary_ip="${eth0_ipv4_list[0]}"
    fi

    local dhcp_summary
    if [[ $NMCLI_AVAILABLE == true ]]; then
        case "$eth0_method" in
            shared)
                if [[ -n "$eth0_primary_ip" ]]; then
                    dhcp_summary="DHCP summary: eth0 is providing DHCP service via NetworkManager shared mode (IP ${eth0_primary_ip})."
                else
                    dhcp_summary="DHCP summary: eth0 is providing DHCP service via NetworkManager shared mode."
                fi
                ;;
            auto)
                if [[ -n "$eth0_primary_ip" ]]; then
                    dhcp_summary="DHCP summary: eth0 is configured as a DHCP client (address ${eth0_primary_ip})."
                else
                    dhcp_summary="DHCP summary: eth0 is configured as a DHCP client (awaiting lease)."
                fi
                ;;
            manual)
                if [[ -n "$eth0_primary_ip" ]]; then
                    dhcp_summary="DHCP summary: eth0 uses a static IPv4 configuration (${eth0_primary_ip})."
                else
                    dhcp_summary="DHCP summary: eth0 uses a static IPv4 configuration with no IPv4 address reported."
                fi
                ;;
            disabled)
                dhcp_summary="DHCP summary: eth0 IPv4 networking is disabled."
                ;;
            "")
                if [[ -z "$eth0_state" ]]; then
                    dhcp_summary="DHCP summary: eth0 device not detected."
                else
                    dhcp_summary="DHCP summary: eth0 has no active IPv4 configuration."
                fi
                ;;
            *)
                dhcp_summary="DHCP summary: eth0 IPv4 method is '$eth0_method'."
                ;;
        esac
    else
        if [[ -n "$eth0_primary_ip" ]]; then
            dhcp_summary="DHCP summary: Unable to determine DHCP role without NetworkManager (current address ${eth0_primary_ip})."
        elif [[ -n "$eth0_state" ]]; then
            dhcp_summary="DHCP summary: Unable to determine DHCP role without NetworkManager."
        else
            dhcp_summary="DHCP summary: eth0 device not detected."
        fi
    fi

    echo "$dhcp_summary"
    if [[ $NMCLI_AVAILABLE == true && "$eth0_method" == "auto" ]]; then
        local detected_server=""
        detected_server=$(discover_dhcp_server eth0 2>/dev/null || true)
        if [[ -n "$detected_server" ]]; then
            echo "Detected DHCP server for eth0: $detected_server"
        fi
    fi
    echo ""
    echo "Network configuration summary:"
    describe_device_status eth0
    describe_device_status wlan0
    describe_device_status wlan1

    echo ""
    echo "Network service status:"
    describe_service_status NetworkManager "NetworkManager"
    describe_service_status ssh "SSH daemon"
    describe_service_status wlan1-refresh "wlan1 refresh"
    describe_service_status nginx "nginx"
}

discover_dhcp_server() {
    local dev="$1"
    local server=""

    local options=""
    options=$(nmcli -g DHCP4.OPTION device show "$dev" 2>/dev/null || true)
    if [[ -n "$options" ]]; then
        server=$(awk -F'=' '/dhcp_server_identifier/ {gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2); print $2; exit}' <<<"$options")
    fi

    if [[ -z "$server" ]]; then
        server=$(nmcli -g IP4.GATEWAY device show "$dev" 2>/dev/null | head -n1 || true)
    fi

    if [[ -z "$server" ]]; then
        server=$(ip route show dev "$dev" 2>/dev/null | awk '/default/ {print $3; exit}' || true)
    fi

    if [[ -z "$server" ]]; then
        local candidate
        for candidate in 192.168.129.1 192.168.1.1; do
            if ping -c1 -W1 "$candidate" >/dev/null 2>&1; then
                server="$candidate"
                break
            fi
        done
    fi

    if [[ -n "$server" ]]; then
        printf '%s\n' "$server"
        return 0
    fi

    return 1
}

discover_gateway_address() {
    local dev="$1"
    local gateway=""

    gateway=$(nmcli -g IP4.GATEWAY device show "$dev" 2>/dev/null | head -n1 || true)
    if [[ -n "$gateway" ]]; then
        printf '%s\n' "$gateway"
        return 0
    fi

    local options=""
    options=$(nmcli -g DHCP4.OPTION device show "$dev" 2>/dev/null || true)
    if [[ -n "$options" ]]; then
        gateway=$(awk -F'=' '/option routers/ {gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2); split($2, routers, ","); gsub(/^[[:space:]]+|[[:space:]]+$/, "", routers[1]); print routers[1]; exit}' <<<"$options")
    fi

    if [[ -z "$gateway" ]]; then
        gateway=$(ip route show dev "$dev" 2>/dev/null | awk '/default/ {print $3; exit}' || true)
    fi

    if [[ -z "$gateway" ]]; then
        local candidate
        for candidate in 192.168.129.1 192.168.1.1; do
            if ping -c1 -W1 "$candidate" >/dev/null 2>&1; then
                gateway="$candidate"
                break
            fi
        done
    fi

    if [[ -n "$gateway" ]]; then
        printf '%s\n' "$gateway"
        return 0
    fi

    return 1
}

ensure_dhcp_client_registration() {
    local dev="$1"
    local wait_seconds="${2:-15}"
    local elapsed=0
    local lease_ip=""

    nmcli device connect "$dev" >/dev/null 2>&1 || true

    while (( elapsed < wait_seconds )); do
        lease_ip=$(nmcli -g IP4.ADDRESS device show "$dev" 2>/dev/null | head -n1 || true)
        if [[ -n "$lease_ip" ]]; then
            break
        fi
        sleep 1
        ((elapsed++))
    done

    if [[ -n "$lease_ip" ]]; then
        echo "Confirmed IPv4 lease on $dev ($lease_ip)."
    else
        echo "Warning: No IPv4 lease reported on $dev after waiting ${wait_seconds}s." >&2
    fi

    ETH0_DHCP_SERVER=$(discover_dhcp_server "$dev" || true)
    if [[ -n "$ETH0_DHCP_SERVER" ]]; then
        echo "Detected DHCP server for $dev at $ETH0_DHCP_SERVER."
    else
        echo "Warning: Unable to detect DHCP server for $dev; checked fallback addresses 192.168.129.1 and 192.168.1.1." >&2
    fi
}
validate_subnet_octet() {
    local value="$1"
    if [[ ! "$value" =~ ^[0-9]+$ ]]; then
        echo "Error: --subnet requires an integer between 0 and 254." >&2
        exit 1
    fi
    if (( value < 0 || value > 254 )); then
        echo "Error: --subnet requires an integer between 0 and 254." >&2
        exit 1
    fi
}
validate_subnet_triplet() {
    local value="$1"
    local IFS='.'
    read -r -a octets <<< "$value"
    if [[ ${#octets[@]} -ne 3 ]]; then
        echo "Error: --subnet requires three octets in the form X.Y.Z." >&2
        exit 1
    fi
    for i in "${!octets[@]}"; do
        local octet="${octets[$i]}"
        if [[ ! "$octet" =~ ^[0-9]+$ ]]; then
            echo "Error: --subnet octets must be integers between 0 and 255." >&2
            exit 1
        fi
        if (( octet < 0 || octet > 255 )); then
            echo "Error: --subnet octets must be integers between 0 and 255." >&2
            exit 1
        fi
    done
    if (( octets[2] > 254 )); then
        echo "Error: --subnet third octet must be between 0 and 254." >&2
        exit 1
    fi
}
validate_prefix_value() {
    local value="$1"
    if [[ ! "$value" =~ ^[0-9]+$ ]]; then
        echo "Error: --subnet prefix must be 16 or 24." >&2
        exit 1
    fi
    if [[ "$value" != "16" && "$value" != "24" ]]; then
        echo "Error: --subnet prefix must be 16 or 24." >&2
        exit 1
    fi
}
set_subnet_and_prefix() {
    local value="$1"
    local prefix="$ETH0_PREFIX"
    local subnet="$value"
    if [[ "$value" == */* ]]; then
        subnet="${value%%/*}"
        prefix="${value##*/}"
        if [[ -z "$subnet" || -z "$prefix" ]]; then
            echo "Error: --subnet requires a value in the form Z, X.Y.Z, Z/P, or X.Y.Z/P." >&2
            exit 1
        fi
    fi
    if [[ -z "$subnet" ]]; then
        echo "Error: --subnet requires a value in the form Z, X.Y.Z, Z/P, or X.Y.Z/P." >&2
        exit 1
    fi
    if [[ "$subnet" =~ ^[0-9]+$ ]]; then
        validate_subnet_octet "$subnet"
        ETH0_SUBNET_BASE="${DEFAULT_ETH0_SUBNET_PREFIX}.${subnet}"
    elif [[ "$subnet" =~ ^[0-9]+(\.[0-9]+){2}$ ]]; then
        validate_subnet_triplet "$subnet"
        ETH0_SUBNET_BASE="$subnet"
    else
        echo "Error: --subnet requires a value in the form Z, X.Y.Z, Z/P, or X.Y.Z/P." >&2
        exit 1
    fi
    validate_prefix_value "$prefix"
    ETH0_PREFIX="$prefix"
}

validate_eth0_mode() {
    local value="$1"
    case "$value" in
        shared|client|auto)
            ;;
        *)
            echo "Error: --eth0-mode must be one of shared, client, or auto." >&2
            exit 1
            ;;
    esac
}

perform_dhcp_reset_without_nmcli() {
    echo "NetworkManager (nmcli) is not installed; attempting a generic DHCP reset."

    local -a ethernet_devices=()
    mapfile -t ethernet_devices < <(ip -o link show 2>/dev/null | awk -F': ' '/^[0-9]+: e/{print $2}' || true)

    if (( ${#ethernet_devices[@]} == 0 )); then
        echo "No ethernet interfaces detected to reset." >&2
    fi

    local dev
    for dev in "${ethernet_devices[@]}"; do
        if [[ "$dev" == "lo" ]]; then
            continue
        fi
        if ip link show "$dev" >/dev/null 2>&1; then
            ip addr flush dev "$dev" >/dev/null 2>&1 || true
            if command -v dhclient >/dev/null 2>&1; then
                dhclient -r "$dev" >/dev/null 2>&1 || true
                dhclient "$dev" >/dev/null 2>&1 || true
            fi
        fi
    done

    if command -v netplan >/dev/null 2>&1; then
        if netplan apply >/dev/null 2>&1; then
            echo "Applied netplan configuration to refresh DHCP settings."
        else
            echo "Warning: Failed to apply netplan configuration; verify system networking." >&2
        fi
    fi

    echo "DHCP reset complete (NetworkManager unavailable)."
    print_network_status || true
    exit 0
}

perform_dhcp_reset() {
    echo "Restoring upstream DHCP defaults..."

    if [[ $NMCLI_AVAILABLE == false ]]; then
        perform_dhcp_reset_without_nmcli
    fi

    local removed=false
    if [ -f /etc/systemd/system/wlan1-device-refresh.service ]; then
        systemctl stop wlan1-device-refresh >/dev/null 2>&1 || true
        systemctl disable wlan1-device-refresh >/dev/null 2>&1 || true
        rm -f /etc/systemd/system/wlan1-device-refresh.service
        systemctl daemon-reload >/dev/null 2>&1 || true
        echo "Removed legacy wlan1-device-refresh service definition."
        removed=true
    fi

    local conn
    for conn in "$ETH0_CONNECTION_SHARED" "$ETH0_CONNECTION_CLIENT"; do
        if nmcli -t -f NAME connection show | grep -Fxq "$conn"; then
            nmcli connection delete "$conn" >/dev/null 2>&1 || true
            echo "Removed NetworkManager connection '$conn'."
            removed=true
        fi
    done

    local -a ethernet_connections=()
    mapfile -t ethernet_connections < <(nmcli -t -f NAME,TYPE connection show 2>/dev/null | awk -F: '$2=="802-3-ethernet" {print $1}')
    for conn in "${ethernet_connections[@]}"; do
        local method
        method=$(nmcli -g ipv4.method connection show "$conn" 2>/dev/null || true)
        if [[ "$method" == "shared" || "$method" == "manual" ]]; then
            nmcli connection modify "$conn" \
                ipv4.method auto \
                ipv4.never-default no \
                ipv4.may-fail yes \
                ipv4.route-metric 100 \
                ipv6.method auto \
                ipv6.never-default no \
                ipv6.may-fail yes \
                ipv6.route-metric 100 >/dev/null 2>&1 || true
            echo "Reset ethernet connection '$conn' to request DHCP."
            removed=true
        fi
    done

    nmcli connection reload >/dev/null 2>&1 || true

    local -a ethernet_devices=()
    mapfile -t ethernet_devices < <(nmcli -t -f DEVICE,TYPE device status 2>/dev/null | awk -F: '$2=="ethernet" {print $1}')
    local dev
    for dev in "${ethernet_devices[@]}"; do
        if ip link show "$dev" >/dev/null 2>&1; then
            ip addr flush dev "$dev" >/dev/null 2>&1 || true
            nmcli device set "$dev" managed yes >/dev/null 2>&1 || true
            if ! nmcli device reconnect "$dev" >/dev/null 2>&1; then
                nmcli device connect "$dev" >/dev/null 2>&1 || true
            fi
        fi
    done

    if [[ $removed == false && ${#ethernet_devices[@]} -eq 0 ]]; then
        echo "No NetworkManager ethernet configuration detected to reset." >&2
    fi

    echo "DHCP reset complete."
    print_network_status || true
    exit 0
}

while [[ $# -gt 0 ]]; do
    if [[ $DHCP_RESET == true ]]; then
        echo "Error: --dhcp-reset cannot be combined with other options." >&2
        exit 1
    fi
    case "$1" in
        --password)
            if [[ $AP_SET_PASSWORD == true ]]; then
                echo "Error: --ap-set-password cannot be combined with other options." >&2
                exit 1
            fi
            OTHER_OPTIONS_USED=true
            FORCE_PASSWORD=true
            ;;
        --port)
            if [[ $AP_SET_PASSWORD == true ]]; then
                echo "Error: --ap-set-password cannot be combined with other options." >&2
                exit 1
            fi
            if [[ $# -lt 2 ]]; then
                echo "Error: --port requires a value." >&2
                exit 1
            fi
            REQUESTED_BACKEND_PORT="$2"
            PORT_CHANGE_REQUESTED=true
            OTHER_OPTIONS_USED=true
            shift
            ;;
        --port=*)
            if [[ $AP_SET_PASSWORD == true ]]; then
                echo "Error: --ap-set-password cannot be combined with other options." >&2
                exit 1
            fi
            REQUESTED_BACKEND_PORT="${1#--port=}"
            PORT_CHANGE_REQUESTED=true
            OTHER_OPTIONS_USED=true
            ;;
        --constellation)
            if [[ $AP_SET_PASSWORD == true ]]; then
                echo "Error: --ap-set-password cannot be combined with other options." >&2
                exit 1
            fi
            if [[ $# -lt 2 ]]; then
                echo "Error: --constellation requires a hub value." >&2
                exit 1
            fi
            CONSTELLATION_HUB="$2"
            OTHER_OPTIONS_USED=true
            shift
            ;;
        --constellation=*)
            if [[ $AP_SET_PASSWORD == true ]]; then
                echo "Error: --ap-set-password cannot be combined with other options." >&2
                exit 1
            fi
            CONSTELLATION_HUB="${1#--constellation=}"
            OTHER_OPTIONS_USED=true
            ;;
        --ap)
            if [[ $AP_SET_PASSWORD == true ]]; then
                echo "Error: --ap-set-password cannot be combined with other options." >&2
                exit 1
            fi
            if [[ $# -lt 2 ]]; then
                echo "Error: --ap requires a name." >&2
                exit 1
            fi
            AP_NAME="$2"
            if [[ -z "$AP_NAME" ]]; then
                echo "Error: --ap requires a non-empty name." >&2
                exit 1
            fi
            AP_SPECIFIED=true
            OTHER_OPTIONS_USED=true
            shift
            ;;
        --ap=*)
            if [[ $AP_SET_PASSWORD == true ]]; then
                echo "Error: --ap-set-password cannot be combined with other options." >&2
                exit 1
            fi
            AP_NAME="${1#--ap=}"
            if [[ -z "$AP_NAME" ]]; then
                echo "Error: --ap requires a non-empty name." >&2
                exit 1
            fi
            AP_SPECIFIED=true
            OTHER_OPTIONS_USED=true
            ;;
        --subnet)
            if [[ $AP_SET_PASSWORD == true ]]; then
                echo "Error: --ap-set-password cannot be combined with other options." >&2
                exit 1
            fi
            if [[ $# -lt 2 ]]; then
                echo "Error: --subnet requires a value." >&2
                exit 1
            fi
            set_subnet_and_prefix "$2"
            ETH0_SUBNET_SPECIFIED=true
            OTHER_OPTIONS_USED=true
            shift
            ;;
        --subnet=*)
            if [[ $AP_SET_PASSWORD == true ]]; then
                echo "Error: --ap-set-password cannot be combined with other options." >&2
                exit 1
            fi
            subnet_value="${1#--subnet=}"
            set_subnet_and_prefix "$subnet_value"
            ETH0_SUBNET_SPECIFIED=true
            OTHER_OPTIONS_USED=true
            ;;
        --eth0-mode)
            if [[ $AP_SET_PASSWORD == true ]]; then
                echo "Error: --ap-set-password cannot be combined with other options." >&2
                exit 1
            fi
            if [[ -n "$DHCP_OVERRIDE" ]]; then
                echo "Error: --eth0-mode cannot be combined with --dhcp-server or --dhcp-client." >&2
                exit 1
            fi
            if [[ $# -lt 2 ]]; then
                echo "Error: --eth0-mode requires a value." >&2
                exit 1
            fi
            validate_eth0_mode "$2"
            ETH0_MODE="$2"
            ETH0_MODE_SPECIFIED=true
            OTHER_OPTIONS_USED=true
            shift
            ;;
        --eth0-mode=*)
            if [[ $AP_SET_PASSWORD == true ]]; then
                echo "Error: --ap-set-password cannot be combined with other options." >&2
                exit 1
            fi
            mode_value="${1#--eth0-mode=}"
            if [[ -n "$DHCP_OVERRIDE" ]]; then
                echo "Error: --eth0-mode cannot be combined with --dhcp-server or --dhcp-client." >&2
                exit 1
            fi
            validate_eth0_mode "$mode_value"
            ETH0_MODE="$mode_value"
            ETH0_MODE_SPECIFIED=true
            OTHER_OPTIONS_USED=true
            ;;
        --no-firewall)
            if [[ $AP_SET_PASSWORD == true ]]; then
                echo "Error: --ap-set-password cannot be combined with other options." >&2
                exit 1
            fi
            SKIP_FIREWALL=true
            OTHER_OPTIONS_USED=true
            ;;
        --no-ap)
            if [[ $AP_SET_PASSWORD == true ]]; then
                echo "Error: --ap-set-password cannot be combined with other options." >&2
                exit 1
            fi
            SKIP_AP=true
            OTHER_OPTIONS_USED=true
            ;;
        --unsafe)
            if [[ $AP_SET_PASSWORD == true ]]; then
                echo "Error: --ap-set-password cannot be combined with other options." >&2
                exit 1
            fi
            UNSAFE=true
            OTHER_OPTIONS_USED=true
            ;;
        -i|--interactive)
            if [[ $AP_SET_PASSWORD == true ]]; then
                echo "Error: --ap-set-password cannot be combined with other options." >&2
                exit 1
            fi
            INTERACTIVE=true
            OTHER_OPTIONS_USED=true
            ;;
        --vnc)
            if [[ $AP_SET_PASSWORD == true ]]; then
                echo "Error: --ap-set-password cannot be combined with other options." >&2
                exit 1
            fi
            REQUIRE_VNC=true
            VNC_OPTION_SET=true
            OTHER_OPTIONS_USED=true
            ;;
        --no-vnc)
            if [[ $AP_SET_PASSWORD == true ]]; then
                echo "Error: --ap-set-password cannot be combined with other options." >&2
                exit 1
            fi
            REQUIRE_VNC=false
            VNC_OPTION_SET=true
            OTHER_OPTIONS_USED=true
            ;;
        --dhcp-server)
            if [[ $AP_SET_PASSWORD == true ]]; then
                echo "Error: --ap-set-password cannot be combined with other options." >&2
                exit 1
            fi
            if [[ -n "$DHCP_OVERRIDE" ]]; then
                echo "Error: --dhcp-server cannot be combined with --dhcp-client." >&2
                exit 1
            fi
            if [[ $ETH0_MODE_SPECIFIED == true ]]; then
                echo "Error: --dhcp-server cannot be combined with --eth0-mode." >&2
                exit 1
            fi
            DHCP_OVERRIDE="shared"
            ETH0_MODE="shared"
            ETH0_MODE_SPECIFIED=true
            OTHER_OPTIONS_USED=true
            ;;
        --dhcp-client)
            if [[ $AP_SET_PASSWORD == true ]]; then
                echo "Error: --ap-set-password cannot be combined with other options." >&2
                exit 1
            fi
            if [[ -n "$DHCP_OVERRIDE" ]]; then
                echo "Error: --dhcp-client cannot be combined with --dhcp-server." >&2
                exit 1
            fi
            if [[ $ETH0_MODE_SPECIFIED == true ]]; then
                echo "Error: --dhcp-client cannot be combined with --eth0-mode." >&2
                exit 1
            fi
            DHCP_OVERRIDE="client"
            ETH0_MODE="client"
            ETH0_MODE_SPECIFIED=true
            OTHER_OPTIONS_USED=true
            ;;
        --dhcp-reset)
            if [[ $AP_SET_PASSWORD == true ]]; then
                echo "Error: --ap-set-password cannot be combined with other options." >&2
                exit 1
            fi
            if [[ $STATUS_ONLY == true || $OTHER_OPTIONS_USED == true ]]; then
                echo "Error: --dhcp-reset cannot be combined with other options." >&2
                exit 1
            fi
            DHCP_RESET=true
            ;;
        --check-port)
            if [[ $AP_SET_PASSWORD == true ]]; then
                echo "Error: --ap-set-password cannot be combined with other options." >&2
                exit 1
            fi
            CHECK_PORT_ONLY=true
            ;;
        --status)
            if [[ $AP_SET_PASSWORD == true ]]; then
                echo "Error: --ap-set-password cannot be combined with other options." >&2
                exit 1
            fi
            STATUS_ONLY=true
            ;;
        --ap-set-password)
            if [[ $AP_SET_PASSWORD == true ]]; then
                echo "Error: --ap-set-password can only be specified once." >&2
                exit 1
            fi
            if [[ $OTHER_OPTIONS_USED == true ]]; then
                echo "Error: --ap-set-password cannot be combined with other options." >&2
                exit 1
            fi
            AP_SET_PASSWORD=true
            ;;
        -h|--help)
            if [[ $AP_SET_PASSWORD == true ]]; then
                echo "Error: --ap-set-password cannot be combined with other options." >&2
                exit 1
            fi
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
    shift
done

BACKEND_PORT="$(detect_backend_port)"

if [[ $CHECK_PORT_ONLY == true ]]; then
    if [[ $AP_SET_PASSWORD == true || $PORT_CHANGE_REQUESTED == true || $OTHER_OPTIONS_USED == true || $STATUS_ONLY == true || $DHCP_RESET == true ]]; then
        echo "Error: --check-port cannot be combined with other options." >&2
        exit 1
    fi
    report_backend_port_sources "$BACKEND_PORT"
    exit 0
fi

if [[ $STATUS_ONLY == true ]]; then
    if [[ $AP_SET_PASSWORD == true ]]; then
        echo "Error: --status cannot be combined with --ap-set-password." >&2
        exit 1
    fi
    if [[ $OTHER_OPTIONS_USED == true ]]; then
        echo "Error: --status cannot be combined with other options." >&2
        exit 1
    fi
    print_network_status
    exit 0
fi

if [[ $DHCP_RESET == true ]]; then
    if [[ $EUID -ne 0 ]]; then
        echo "This script must be run as root" >&2
        exit 1
    fi
    if [[ $UNSAFE == false ]]; then
        echo "Error: --dhcp-reset requires --unsafe to modify network connections." >&2
        exit 1
    fi
    perform_dhcp_reset
fi

if [[ $EUID -ne 0 ]]; then
    echo "This script must be run as root" >&2
    exit 1
fi

if [[ $AP_SET_PASSWORD == true && $OTHER_OPTIONS_USED == true ]]; then
    echo "Error: --ap-set-password cannot be combined with other options." >&2
    exit 1
fi

if [[ $PORT_CHANGE_REQUESTED == true ]]; then
    apply_backend_port_change "$REQUESTED_BACKEND_PORT"
fi

restore_constellation_devices_from_manifest

# Record wlan1 presence for later routing rules and provide early feedback.
if command -v nmcli >/dev/null 2>&1; then
    if ! nmcli -t -f DEVICE device status 2>/dev/null | grep -Fxq "wlan1"; then
        echo "Warning: device wlan1 not found; wlan0 and eth0 clients will not have upstream internet access." >&2
        WLAN1_DETECTED=false
    fi
fi

# Ensure D-Bus and NetworkManager services are available
ensure_service() {
    local svc="$1"
    systemctl enable "$svc" >/dev/null 2>&1 || true
    if ! systemctl is-active --quiet "$svc"; then
        echo "Starting $svc service..."
        systemctl start "$svc" >/dev/null 2>&1 || echo "Warning: unable to start $svc" >&2
    fi
}

ensure_forwarding_rule() {
    local ingress="$1"
    local egress="$2"
    if ! command -v iptables >/dev/null 2>&1; then
        return
    fi
    iptables -C FORWARD -i "$ingress" -o "$egress" -j ACCEPT 2>/dev/null || \
        iptables -A FORWARD -i "$ingress" -o "$egress" -j ACCEPT
    iptables -C FORWARD -i "$egress" -o "$ingress" -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || \
        iptables -A FORWARD -i "$egress" -o "$ingress" -m state --state RELATED,ESTABLISHED -j ACCEPT
}

ensure_masquerade_rule() {
    local egress="$1"
    if ! command -v iptables >/dev/null 2>&1; then
        return
    fi
    iptables -t nat -C POSTROUTING -o "$egress" -j MASQUERADE 2>/dev/null || \
        iptables -t nat -A POSTROUTING -o "$egress" -j MASQUERADE
}

normalize_systemd_networkd_default_route() {
    local iface="$1"
    if [[ -z "$iface" ]]; then
        return
    fi

    if ! command -v ip >/dev/null 2>&1; then
        echo "ip utility not available; skipping systemd-networkd normalization." >&2
        return
    fi

    local ipv4_address
    ipv4_address=$(ip -4 addr show "$iface" | awk '/ inet / {print $2; exit}')
    if [[ -z "$ipv4_address" ]]; then
        echo "Unable to determine IPv4 address for $iface; skipping systemd-networkd normalization." >&2
        return
    fi

    local -a gateways=()
    while IFS= read -r candidate; do
        [[ -z "$candidate" ]] && continue
        if [[ ! "$candidate" =~ ^([0-9]+\.){3}[0-9]+$ ]]; then
            continue
        fi
        local already_present=false
        for existing in "${gateways[@]}"; do
            if [[ "$existing" == "$candidate" ]]; then
                already_present=true
                break
            fi
        done
        if [[ $already_present == false ]]; then
            gateways+=("$candidate")
        fi
    done < <(ip -4 route show default dev "$iface" 2>/dev/null | awk '($1=="default" && $2=="via") {print $3}')

    if (( ${#gateways[@]} == 0 )); then
        echo "Unable to determine default gateway for $iface; skipping systemd-networkd normalization." >&2
        return
    fi

    local gateway=""
    for candidate in "${gateways[@]}"; do
        if [[ "$candidate" =~ ^([0-9]+\.){3}1$ ]]; then
            gateway="$candidate"
            break
        fi
    done

    if [[ -z "$gateway" ]]; then
        gateway="${gateways[0]}"
    fi

    if (( ${#gateways[@]} > 1 )); then
        local joined_gateways
        joined_gateways=$(join_by ", " "${gateways[@]}")
        echo "Multiple default gateways detected for $iface ($joined_gateways); preferring $gateway." >&2
    fi

    local config_path="/etc/systemd/network/10-${iface}.network"
    local backup_path="${config_path}.bak.$(date +%s)"
    if [ -f "$config_path" ]; then
        cp "$config_path" "$backup_path"
        echo "Existing $config_path backed up to $backup_path." >&2
    fi

    mapfile -t resolv_dns < <(awk '/^nameserver / {print $2}' /etc/resolv.conf 2>/dev/null | head -n 3)
    if [[ ${#resolv_dns[@]} -eq 0 ]]; then
        resolv_dns=(8.8.8.8 1.1.1.1)
    fi

    {
        echo "[Match]"
        echo "Name=$iface"
        echo
        echo "[Network]"
        echo "Address=$ipv4_address"
        echo "Gateway=$gateway"
        echo "DHCP=no"
        for dns in "${resolv_dns[@]}"; do
            echo "DNS=$dns"
        done
    } > "$config_path"

    chmod 644 "$config_path"

    rm -f /run/systemd/netif/leases/*
    rm -f /var/lib/systemd/network/*
    systemctl restart systemd-networkd >/dev/null 2>&1 || echo "Warning: failed to restart systemd-networkd" >&2
    systemctl restart systemd-resolved >/dev/null 2>&1 || echo "Warning: failed to restart systemd-resolved" >&2
    echo "Normalized systemd-networkd configuration for $iface (static $ipv4_address via $gateway)."
}

# Prompt helper for interactive mode
ask_step() {
    local var="$1"
    local desc="$2"
    local mandatory="${3:-0}"
    if [[ $INTERACTIVE == true ]]; then
        if [[ $mandatory -eq 1 ]]; then
            read -rp "Run step '$desc'? [Y/n] (mandatory) " _ans
            eval "$var=true"
        else
            read -rp "Run step '$desc'? [Y/n] " _ans
            if [[ -z "$_ans" || "$_ans" =~ ^[Yy] ]]; then
                eval "$var=true"
            else
                eval "$var=false"
            fi
        fi
    else
        eval "$var=true"
    fi
}

# Informational helper for safe-mode skips
skip_network_step() {
    local desc="$1"
    local detail="${2:-}"
    if [[ -n "$detail" ]]; then
        echo "Skipping $desc while running in safe mode to preserve $detail. Re-run with --unsafe to allow this step."
    else
        echo "Skipping $desc while running in safe mode. Re-run with --unsafe to allow this step."
    fi
}

# Slugify helper used for connection names
slugify() {
    local input="$1"
    echo "$input" | tr '[:upper:]' '[:lower:]' | sed -e 's/[^a-z0-9]/-/g' -e 's/--*/-/g' -e 's/^-//' -e 's/-$//'
}

managed_sites_conf_path() {
    local service_slug="arthexis"
    local service_lock="$LOCK_DIR/service.lck"

    if [ -f "$service_lock" ]; then
        local service_name
        service_name="$(tr -d '\r\n' < "$service_lock" 2>/dev/null || true)"
        if [[ -n "$service_name" ]]; then
            local slug
            slug="$(slugify "$service_name")"
            if [[ -n "$slug" ]]; then
                service_slug="$slug"
            fi
        fi
    fi

    printf '/etc/nginx/sites-enabled/%s-sites.conf' "$service_slug"
}

apply_managed_nginx_sites() {
    local requested_port="${1:-$BACKEND_PORT}"
    local config_json="$BASE_DIR/scripts/generated/nginx-sites.json"
    local mode="internal"
    if [ -f "$LOCK_DIR/nginx_mode.lck" ]; then
        mode="$(tr '[:upper:]' '[:lower:]' < "$LOCK_DIR/nginx_mode.lck")"
    fi

    local port="$requested_port"

    local helper="$BASE_DIR/scripts/helpers/render_nginx_sites.py"
    local dest_path
    dest_path="$(managed_sites_conf_path)"
    local dest_dir
    dest_dir="$(dirname "$dest_path")"
    local legacy_dir="/etc/nginx/sites-enabled/arthexis-sites.d"
    local legacy_conf_dir="/etc/nginx/conf.d"

    if [ ! -d "$dest_dir" ]; then
        if command -v sudo >/dev/null 2>&1; then
            sudo mkdir -p "$dest_dir" || true
        else
            mkdir -p "$dest_dir" || true
        fi
    fi

    if [ -d "$legacy_dir" ]; then
        if command -v sudo >/dev/null 2>&1; then
            sudo rm -rf "$legacy_dir" || true
        else
            rm -rf "$legacy_dir" || true
        fi
        echo "Removed legacy managed nginx directory at $legacy_dir to avoid nginx include errors."
    fi

    if compgen -G "$legacy_conf_dir/arthexis-site-*.conf" >/dev/null; then
        if command -v sudo >/dev/null 2>&1; then
            sudo rm -f "$legacy_conf_dir"/arthexis-site-*.conf "$legacy_conf_dir"/arthexis-sites.conf 2>/dev/null || true
        else
            rm -f "$legacy_conf_dir"/arthexis-site-*.conf "$legacy_conf_dir"/arthexis-sites.conf 2>/dev/null || true
        fi
        echo "Removed legacy managed nginx configs from $legacy_conf_dir to avoid conflicts."
    fi

    if [ ! -f "$helper" ]; then
        echo "Managed site helper not found at $helper; skipping." >&2
        return 1
    fi

    if ! command -v python3 >/dev/null 2>&1; then
        echo "python3 not available; skipping managed site configuration." >&2
        return 1
    fi

    if ! command -v nginx >/dev/null 2>&1; then
        echo "nginx not available; skipping managed site configuration." >&2
        return 1
    fi

    local args=("$helper" "--mode" "$mode" "--port" "$port" "--dest" "$dest_path" "--config" "$config_json")
    python3 "${args[@]}"
    local status=$?

    if [ $status -eq 2 ]; then
        if nginx -t; then
            systemctl reload nginx || echo "Warning: nginx reload failed" >&2
        else
            echo "Warning: nginx configuration test failed after applying managed sites" >&2
        fi
    elif [ $status -ne 0 ]; then
        echo "Warning: managed site configuration script exited with status $status" >&2
    fi
}

# Clear any saved WiFi secrets so the connection can run as an open network
# when switching an existing AP profile into public mode. NetworkManager keeps
# legacy WEP keys around even after changing the key management setting to
# "none" which results in the activation prompt that surfaced in the bug
# report. Explicitly blanking those properties prevents the prompt and lets the
# AP come up without credentials.
clear_wifi_secrets() {
    local conn_name="$1"

    nmcli connection modify "$conn_name" wifi-sec.key-mgmt none >/dev/null 2>&1 || true
    nmcli connection modify "$conn_name" wifi-sec.auth-alg open >/dev/null 2>&1 || true

    local -a remove_props=(
        wifi-sec.psk
        wifi-sec.wep-key-type
        wifi-sec.wep-key-flags
        wifi-sec.wep-tx-keyidx
    )
    local prop
    for prop in "${remove_props[@]}"; do
        nmcli connection modify "$conn_name" "-$prop" >/dev/null 2>&1 || true
    done

    local idx
    for idx in 0 1 2 3; do
        nmcli connection modify "$conn_name" "-wifi-sec.wep-key${idx}" >/dev/null 2>&1 || true
    done
}

# Find an existing access point connection that should be reused when
# --ap is not explicitly provided. Preference order:
#   1. An active wlan0 connection whose name contains "-ap".
#   2. Any stored wifi connection whose name contains "-ap", preferring
#      non-default names.
find_existing_ap_connection() {
    local active_ap
    active_ap=$(nmcli -t -f NAME,DEVICE connection show --active 2>/dev/null |
        awk -F: '$2=="wlan0" && $1 ~ /-ap/ {print $1; exit}' || true)
    if [[ -n "$active_ap" ]]; then
        printf '%s' "$active_ap"
        return 0
    fi

    local candidates
    candidates=$(nmcli -t -f NAME,TYPE connection show 2>/dev/null |
        awk -F: '$2=="802-11-wireless" && $1 ~ /-ap/ {print $1}' || true)
    if [[ -z "$candidates" ]]; then
        return 0
    fi

    local candidate
    while IFS= read -r candidate; do
        [[ -z "$candidate" ]] && continue
        if [[ "$candidate" != "$DEFAULT_AP_NAME" ]]; then
            printf '%s' "$candidate"
            return 0
        fi
    done <<< "$candidates"

    candidate=$(printf '%s\n' "$candidates" | head -n1)
    if [[ -n "$candidate" ]]; then
        printf '%s' "$candidate"
    fi
    return 0
}

detect_eth0_mode_auto() {
    local detected="shared"
    local reason=""
    local active_conn
    active_conn=$(nmcli -t -f NAME,DEVICE connection show --active 2>/dev/null |
        awk -F: '$2=="eth0" {print $1; exit}' || true)
    if [[ -n "$active_conn" ]]; then
        local method
        method=$(nmcli -g ipv4.method connection show "$active_conn" 2>/dev/null | head -n1 || true)
        if [[ "$method" == "auto" ]]; then
            detected="client"
            reason="active connection '$active_conn'"
        elif [[ "$method" == "shared" ]]; then
            detected="shared"
            reason="active connection '$active_conn'"
        fi
    fi

    if [[ -z "$reason" && "$PROTECTED_DEV" == "eth0" ]]; then
        detected="client"
        reason="default route via eth0"
    fi

    if [[ -z "$reason" ]]; then
        if nmcli -t -f NAME connection show 2>/dev/null | grep -Fxq "$ETH0_CONNECTION_CLIENT"; then
            local stored_method
            stored_method=$(nmcli -g ipv4.method connection show "$ETH0_CONNECTION_CLIENT" 2>/dev/null | head -n1 || true)
            if [[ "$stored_method" == "auto" ]]; then
                detected="client"
                reason="stored profile '$ETH0_CONNECTION_CLIENT'"
            fi
        fi
    fi

    ETH0_MODE_EFFECTIVE="$detected"
    if [[ -n "$reason" ]]; then
        echo "Auto-detected eth0 mode '$ETH0_MODE_EFFECTIVE' based on $reason."
    elif [[ "$ETH0_MODE_EFFECTIVE" == "client" ]]; then
        echo "Auto-detected eth0 mode 'client'."
    else
        echo "Auto-detected eth0 mode 'shared'."
    fi
}

if [[ $AP_SET_PASSWORD == true ]]; then
    if ! command -v nmcli >/dev/null 2>&1; then
        echo "nmcli (NetworkManager) is required." >&2
        exit 1
    fi

    TARGET_AP="$(find_existing_ap_connection)"
    if [[ -z "$TARGET_AP" ]]; then
        if nmcli -t -f NAME connection show 2>/dev/null | grep -Fxq "$DEFAULT_AP_NAME"; then
            TARGET_AP="$DEFAULT_AP_NAME"
        fi
    fi

    if [[ -z "$TARGET_AP" ]]; then
        echo "Error: Unable to find an access point connection to update." >&2
        exit 1
    fi

    conn_type=$(nmcli -t -f connection.type connection show "$TARGET_AP" 2>/dev/null || true)
    if [[ "$conn_type" != "802-11-wireless" ]]; then
        echo "Error: Connection '$TARGET_AP' is not a WiFi access point." >&2
        exit 1
    fi

    wifi_mode=$(nmcli -t -f wifi.mode connection show "$TARGET_AP" 2>/dev/null || true)
    if [[ "$wifi_mode" != "ap" ]]; then
        echo "Error: Connection '$TARGET_AP' is not configured as an access point." >&2
        exit 1
    fi

    while true; do
        read -rsp "Enter new WiFi password for '$TARGET_AP': " NEW_WIFI_PASS1; echo
        read -rsp "Confirm password: " NEW_WIFI_PASS2; echo
        if [[ -n "$NEW_WIFI_PASS1" && "$NEW_WIFI_PASS1" == "$NEW_WIFI_PASS2" ]]; then
            break
        fi
        echo "Passwords do not match or are empty." >&2
    done

    nmcli connection modify "$TARGET_AP" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$NEW_WIFI_PASS1"

    if nmcli -t -f NAME connection show --active 2>/dev/null | grep -Fxq "$TARGET_AP"; then
        nmcli connection up "$TARGET_AP" >/dev/null 2>&1 || true
    fi

    echo "Updated WiFi password for access point '$TARGET_AP'."
    exit 0
fi

# Check initial internet connectivity (non-fatal)
check_connectivity() {
    ping -c1 -W2 8.8.8.8 >/dev/null 2>&1
}

# Ensure SSH password authentication is enabled
require_ssh_password() {
    if [[ ! -f /etc/ssh/sshd_config ]]; then
        echo "SSH configuration not found; enable SSH with password login before running this script." >&2
        exit 1
    fi
    if ! grep -Eq '^[[:space:]]*PasswordAuthentication[[:space:]]+yes' /etc/ssh/sshd_config; then
        sed -i 's/^PasswordAuthentication no/PasswordAuthentication yes/' /etc/ssh/sshd_config
        systemctl restart ssh
    fi
}

# Ensure VNC is enabled via raspi-config or an active VNC service
# Assumes VNC service names 'vncserver-x11-serviced' or 'x11vnc' when raspi-config is unavailable
require_vnc_enabled() {
    local -a services=(vncserver-x11-serviced x11vnc)
    local vnc_state=""

    if command -v raspi-config >/dev/null 2>&1; then
        vnc_state=$(raspi-config nonint get_vnc 2>/dev/null || true)
        if [[ "$vnc_state" == "1" ]]; then
            return
        fi
    fi

    for svc in "${services[@]}"; do
        if systemctl is-enabled --quiet "$svc" 2>/dev/null || \
           systemctl is-active --quiet "$svc" 2>/dev/null; then
            if [[ "$vnc_state" != "1" && -n "$vnc_state" ]]; then
                echo "raspi-config reports VNC disabled but service '$svc' is active; continuing." >&2
            fi
            return
        fi
    done

    if [[ -n "$vnc_state" ]]; then
        echo "VNC is disabled in raspi-config or no VNC service is active. Enable it before running this script." >&2
    else
        echo "No enabled VNC service detected. Enable a VNC server before running this script." >&2
    fi
    exit 1
}

INITIAL_CONNECTIVITY=true
if check_connectivity; then
    echo "Internet connectivity detected."
else
    echo "No internet connectivity detected at start; continuing..." >&2
    INITIAL_CONNECTIVITY=false
fi

command -v nmcli >/dev/null 2>&1 || {
    echo "nmcli (NetworkManager) is required." >&2
    exit 1
}

require_ssh_password
if [[ $REQUIRE_VNC == true ]]; then
    require_vnc_enabled
elif [[ $VNC_OPTION_SET == true ]]; then
    echo "Skipping VNC requirement as requested."
fi

if [[ $AP_SPECIFIED == false ]]; then
    AUTO_AP_NAME="$(find_existing_ap_connection)"
    if [[ -n "$AUTO_AP_NAME" ]]; then
        if [[ "$AUTO_AP_NAME" != "$AP_NAME" ]]; then
            echo "Using existing access point connection '$AUTO_AP_NAME'."
        fi
        AP_NAME="$AUTO_AP_NAME"
    fi
fi

# Detect the active internet connection and back it up
PROTECTED_DEV=""
PROTECTED_CONN=""
PROTECTED_CONN_BACKUP=""
if [[ $INITIAL_CONNECTIVITY == true ]]; then
    PROTECTED_DEV=$(ip route get 8.8.8.8 2>/dev/null | awk '/dev/ {for(i=1;i<=NF;i++) if($i=="dev") {print $(i+1); break}}' || true)
    if [[ -n "$PROTECTED_DEV" ]]; then
        PROTECTED_CONN=$(nmcli -t -f NAME,DEVICE connection show --active | awk -F: -v dev="$PROTECTED_DEV" '$2==dev {print $1; exit}')
        if [[ -n "$PROTECTED_CONN" ]]; then
            if [[ $UNSAFE == false ]]; then
                echo "Preserving active connection '$PROTECTED_CONN' on '$PROTECTED_DEV'"
            else
                echo "Detected active connection '$PROTECTED_CONN' on '$PROTECTED_DEV'"
            fi
            PROTECTED_CONN_BACKUP="${PROTECTED_CONN}-backup"
            nmcli connection clone "$PROTECTED_CONN" "$PROTECTED_CONN_BACKUP" >/dev/null 2>&1 || PROTECTED_CONN_BACKUP=""
        fi
    fi
fi

if [[ "$ETH0_MODE" == "auto" ]]; then
    detect_eth0_mode_auto
else
    ETH0_MODE_EFFECTIVE="$ETH0_MODE"
    if [[ "$ETH0_MODE" == "shared" ]]; then
        echo "Forcing eth0 mode 'shared' (DHCP server); skipping automatic detection."
    elif [[ "$ETH0_MODE" == "client" ]]; then
        echo "Forcing eth0 mode 'client' (DHCP client); skipping automatic detection."
    else
        echo "Forcing eth0 mode '$ETH0_MODE'; skipping DHCP detection."
    fi
fi

if [[ "$ETH0_MODE_EFFECTIVE" == "client" ]]; then
    ETH0_CONNECTION_NAME="$ETH0_CONNECTION_CLIENT"
else
    ETH0_CONNECTION_NAME="$ETH0_CONNECTION_SHARED"
fi

if [[ "$ETH0_MODE_EFFECTIVE" == "client" && $ETH0_SUBNET_SPECIFIED == true ]]; then
    echo "Note: --subnet is ignored when eth0 operates in client mode."
fi

# Determine access point name and password before running steps
HYPERLINE_NAME="hyperline"
AP_NAME_LOWER="$(printf '%s' "$AP_NAME" | tr '[:upper:]' '[:lower:]')"
AP_HYPERLINE_BY_USER=false
if [[ "$AP_NAME_LOWER" == "$HYPERLINE_NAME" ]]; then
    AP_HYPERLINE_BY_USER=true
fi
EXISTING_PASS=""
WIFI_PASS=""
if [[ $SKIP_AP == false ]]; then
    EXISTING_PASS="$(nmcli -s -g 802-11-wireless-security.psk connection show "$AP_NAME" 2>/dev/null || true)"
    if [[ -z "$EXISTING_PASS" && "$AP_NAME" != "$DEFAULT_AP_NAME" ]]; then
        EXISTING_PASS="$(nmcli -s -g 802-11-wireless-security.psk connection show "$DEFAULT_AP_NAME" 2>/dev/null || true)"
    fi
    if [[ -z "$EXISTING_PASS" || $FORCE_PASSWORD == true ]]; then
        while true; do
            read -rsp "Enter WiFi password for '$AP_NAME': " WIFI_PASS1; echo
            read -rsp "Confirm password: " WIFI_PASS2; echo
            if [[ "$WIFI_PASS1" == "$WIFI_PASS2" && -n "$WIFI_PASS1" ]]; then
                WIFI_PASS="$WIFI_PASS1"
                break
            else
                echo "Passwords do not match or are empty." >&2
            fi
        done
    else
        WIFI_PASS="$EXISTING_PASS"
    fi
fi

# Detect environments that rely on systemd-networkd instead of NetworkManager.
SYSTEMD_NETWORKD_ACTIVE=false
NETWORKD_DEFAULT_IFACE=""
NETWORKD_DUPLICATE_ROUTES=false
if systemctl is-active --quiet systemd-networkd 2>/dev/null; then
    SYSTEMD_NETWORKD_ACTIVE=true
    if command -v ip >/dev/null 2>&1; then
        DEFAULT_ROUTE_LINE=$(ip -4 route show default | head -n1)
        if [[ -n "$DEFAULT_ROUTE_LINE" ]]; then
            NETWORKD_DEFAULT_IFACE=$(echo "$DEFAULT_ROUTE_LINE" | awk '{for (i=1; i<=NF; i++) if ($i=="dev") {print $(i+1); exit}}')
            if [[ -n "$NETWORKD_DEFAULT_IFACE" ]]; then
                DEFAULT_ROUTE_COUNT=$(ip -4 route show default dev "$NETWORKD_DEFAULT_IFACE" | wc -l | tr -d '[:space:]')
                if [[ ${DEFAULT_ROUTE_COUNT:-0} -gt 1 ]]; then
                    NETWORKD_DUPLICATE_ROUTES=true
                fi
            fi
        fi
    fi
fi

# Collect user decisions for each step in advance
AP_ACTIVATED=false
NMCLI_AVAILABLE=false
if command -v nmcli >/dev/null 2>&1; then
    NMCLI_AVAILABLE=true
fi

ask_step RUN_SERVICES "Ensure required services"
if [[ $SKIP_AP == true ]]; then
    RUN_AP=false
elif [[ $NMCLI_AVAILABLE == true ]]; then
    ask_step RUN_AP "Configure wlan0 access point"
else
    RUN_AP=false
fi
if [[ $NMCLI_AVAILABLE == true ]]; then
ask_step RUN_WLAN1_REFRESH "Install wlan1 device refresh service"
else
    RUN_WLAN1_REFRESH=false
fi

RUN_SYSTEMD_NETWORKD=false
if [[ $SYSTEMD_NETWORKD_ACTIVE == true && $NETWORKD_DUPLICATE_ROUTES == true && $NMCLI_AVAILABLE == false ]]; then
    ask_step RUN_SYSTEMD_NETWORKD "Normalize systemd-networkd default route for $NETWORKD_DEFAULT_IFACE"
fi

RUN_CONSTELLATION=false
if [[ -n "$CONSTELLATION_HUB" ]]; then
    ask_step RUN_CONSTELLATION "Configure Constellation WireGuard overlay for $CONSTELLATION_HUB"
fi

ask_step RUN_PACKAGES "Ensure required packages and SSH service"
ask_step RUN_NGINX_SITES "Apply managed NGINX site configuration"
if [[ $SKIP_FIREWALL == false ]]; then
    ask_step RUN_FIREWALL "Validate firewall ports"
else
    RUN_FIREWALL=false
fi
if [[ $NMCLI_AVAILABLE == true ]]; then
    ask_step RUN_REINSTALL_WLAN1 "Reinstall wlan1 connections"
    ask_step RUN_CONFIGURE_NET "Configure network connections"
    ask_step RUN_ROUTING "Finalize routing and connectivity checks"
else
    RUN_REINSTALL_WLAN1=false
    RUN_CONFIGURE_NET=false
    RUN_ROUTING=false
fi

# Execute steps based on user choices
if [[ $RUN_SERVICES == true ]]; then
    ensure_service dbus
    if [[ $NMCLI_AVAILABLE == true ]]; then
        ensure_service NetworkManager
    fi
fi

if [[ $RUN_SYSTEMD_NETWORKD == true ]]; then
    if [[ $UNSAFE == false ]]; then
        skip_reason=""
        if [[ -n "$PROTECTED_CONN" && -n "$PROTECTED_DEV" ]]; then
            skip_reason="connection '$PROTECTED_CONN' on $PROTECTED_DEV"
        fi
        skip_network_step "systemd-networkd default route normalization for $NETWORKD_DEFAULT_IFACE" "$skip_reason"
        unset skip_reason
    else
        normalize_systemd_networkd_default_route "$NETWORKD_DEFAULT_IFACE"
    fi
fi

if [[ $RUN_AP == true ]]; then
    if [[ $UNSAFE == false ]]; then
        if [[ "$PROTECTED_DEV" == "wlan0" && -n "$PROTECTED_CONN" ]]; then
            skip_network_step "wlan0 access point configuration" "connection '$PROTECTED_CONN' on wlan0"
        else
            skip_network_step "wlan0 access point configuration"
        fi
    elif ! nmcli -t -f DEVICE device status | grep -Fxq "wlan0"; then
        echo "Warning: device wlan0 not found; skipping access point configuration." >&2
    else
        nmcli -t -f NAME,DEVICE connection show | awk -F: -v ap="$AP_NAME" -v hl="$HYPERLINE_NAME" -v protect="$PROTECTED_CONN" '$2=="wlan0" && $1!=ap && $1!=hl && $1!=protect {print $1}' | while read -r con; do
            nmcli connection delete "$con"
        done

        security_args=(wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$WIFI_PASS")

        if nmcli -t -f NAME connection show | grep -Fxq "$AP_NAME"; then
            nmcli connection modify "$AP_NAME" \
                connection.interface-name wlan0 \
                wifi.ssid "$AP_NAME" \
                wifi.mode ap \
                wifi.band bg \
                "${security_args[@]}" \
                ipv4.method shared \
                ipv4.addresses 10.42.0.1/16 \
                ipv4.never-default yes \
                ipv6.method shared \
                ipv6.addresses "$AP_IPV6_ADDRESS" \
                ipv6.never-default yes \
                connection.autoconnect yes \
                connection.autoconnect-priority 0
        else
            nmcli connection add type wifi ifname wlan0 con-name "$AP_NAME" \
                connection.interface-name wlan0 autoconnect yes connection.autoconnect-priority 0 \
                ssid "$AP_NAME" mode ap ipv4.method shared ipv4.addresses 10.42.0.1/16 \
                ipv4.never-default yes ipv6.method shared ipv6.addresses "$AP_IPV6_ADDRESS" ipv6.never-default yes \
                wifi.band bg "${security_args[@]}"
        fi
        if ! nmcli connection up "$AP_NAME" ifname wlan0; then
            echo "Failed to activate access point connection '$AP_NAME' on wlan0." >&2
            exit 1
        fi
        active_ap_device=$(nmcli -g GENERAL.DEVICES connection show "$AP_NAME" 2>/dev/null | tr -d '\n')
        if [[ ",$active_ap_device," != *,wlan0,* ]]; then
            echo "Access point '$AP_NAME' is not bound to wlan0 (device: '${active_ap_device:-none}')." >&2
            exit 1
        fi
        AP_ACTIVATED=true
        if command -v iptables >/dev/null 2>&1; then
            iptables -C INPUT -i wlan0 -d 10.42.0.1 -j ACCEPT 2>/dev/null || \
                iptables -A INPUT -i wlan0 -d 10.42.0.1 -j ACCEPT
            while iptables -C FORWARD -i wlan0 -j DROP 2>/dev/null; do
                iptables -D FORWARD -i wlan0 -j DROP
            done
        fi
        if [[ $WLAN1_DETECTED == true ]]; then
            ensure_forwarding_rule wlan0 wlan1
            ensure_masquerade_rule wlan1
        fi
        if ! nmcli -t -f NAME connection show --active | grep -Fxq "$AP_NAME"; then
            echo "Access point $AP_NAME failed to start." >&2
            exit 1
        fi
        echo "Access point '$AP_NAME' is active on wlan0 with NetworkManager providing shared DHCP (10.42.0.1/16)."
    fi
fi

if [[ $RUN_WLAN1_REFRESH == true ]]; then
    if [[ $UNSAFE == false ]]; then
        if [[ "$PROTECTED_DEV" == "wlan1" && -n "$PROTECTED_CONN" ]]; then
            skip_network_step "wlan1 refresh service installation" "connection '$PROTECTED_CONN' on wlan1"
        else
            skip_network_step "wlan1 refresh service installation"
        fi
    elif ! nmcli -t -f DEVICE device status | grep -Fxq "wlan1"; then
        echo "Warning: device wlan1 not found; skipping wlan1 refresh service." >&2
    else
        WLAN1_REFRESH_SCRIPT="$BASE_DIR/scripts/wlan1-refresh.sh"
        WLAN1_REFRESH_SERVICE="wlan1-refresh"
        WLAN1_REFRESH_SERVICE_FILE="/etc/systemd/system/${WLAN1_REFRESH_SERVICE}.service"
        if [ -f "$WLAN1_REFRESH_SCRIPT" ]; then
            if [ -f /etc/systemd/system/wlan1-device-refresh.service ]; then
                systemctl stop wlan1-device-refresh || true
                systemctl disable wlan1-device-refresh || true
                rm -f /etc/systemd/system/wlan1-device-refresh.service
                systemctl daemon-reload
                echo "Removed legacy wlan1-device-refresh service definition."
            fi
            cat > "$WLAN1_REFRESH_SERVICE_FILE" <<EOF
[Unit]
Description=Refresh wlan1 MAC addresses in NetworkManager
After=NetworkManager.service

[Service]
Type=oneshot
ExecStart=$WLAN1_REFRESH_SCRIPT

[Install]
WantedBy=multi-user.target
EOF
            systemctl daemon-reload
            systemctl enable "$WLAN1_REFRESH_SERVICE" >/dev/null 2>&1 || true
            "$WLAN1_REFRESH_SCRIPT" || true
            echo "wlan1 refresh service '${WLAN1_REFRESH_SERVICE}' installed and enabled."
        fi
    fi
fi

if [[ $RUN_PACKAGES == true ]]; then
    APT_UPDATED=false
    ensure_pkg() {
        local cmd="$1"
        local pkg="$2"
        if ! command -v "$cmd" >/dev/null 2>&1; then
            if [ "$APT_UPDATED" = false ]; then
                if ! apt-get update; then
                    echo "Warning: apt-get update failed; continuing without package installation" >&2
                    return
                fi
                APT_UPDATED=true
            fi
            if ! apt-get install -y "$pkg"; then
                echo "Warning: failed to install $pkg" >&2
            fi
        fi
    }

    ensure_pkg nginx nginx
    ensure_pkg sshd openssh-server
    ensure_pkg wg wireguard-tools
    ensure_service ssh

    if command -v ufw >/dev/null 2>&1; then
        STATUS=$(ufw status 2>/dev/null || true)
        if ! echo "$STATUS" | grep -iq "inactive"; then
            ufw allow 22/tcp || true
        fi
    fi
fi

if [[ $RUN_CONSTELLATION == true ]]; then
    if [[ $UNSAFE == false ]]; then
        skip_network_step "Constellation WireGuard configuration" "hub $CONSTELLATION_HUB"
    else
        if ! configure_constellation_wireguard "$CONSTELLATION_HUB"; then
            echo "Constellation WireGuard configuration failed." >&2
            exit 1
        fi
    fi
fi

if [[ $RUN_NGINX_SITES == true ]]; then
    apply_managed_nginx_sites "$BACKEND_PORT"
fi

if [[ $RUN_FIREWALL == true ]]; then
    PORTS=(22 21114 8554)
    MODE="internal"
    if [ -f "$LOCK_DIR/nginx_mode.lck" ]; then
        MODE="$(cat "$LOCK_DIR/nginx_mode.lck")"
    fi
    if [ "$MODE" = "public" ]; then
        PORTS+=(80 443 "$BACKEND_PORT")
    else
        PORTS+=(8000 8080 "$BACKEND_PORT")
    fi

    if command -v ufw >/dev/null 2>&1; then
        STATUS=$(ufw status 2>/dev/null || true)
        if echo "$STATUS" | grep -iq "inactive"; then
            :
        else
            for p in "${PORTS[@]}"; do
                if ! echo "$STATUS" | grep -q "${p}"; then
                    echo "Port $p is not allowed through the firewall" >&2
                    exit 1
                fi
            done
        fi
    fi
fi

if [[ $RUN_REINSTALL_WLAN1 == true ]]; then
    if [[ $UNSAFE == false ]]; then
        if [[ "$PROTECTED_DEV" == "wlan1" && -n "$PROTECTED_CONN" ]]; then
            skip_network_step "wlan1 connection reinstall" "connection '$PROTECTED_CONN' on wlan1"
        else
            skip_network_step "wlan1 connection reinstall"
        fi
    elif nmcli -t -f DEVICE device status | grep -Fxq "wlan1"; then
        declare -A SEEN_SLUGS=()
        nmcli device disconnect wlan1 || true
        while IFS= read -r con; do
            if [[ $UNSAFE == false && "$con" == "$PROTECTED_CONN" ]]; then
                continue
            fi
            iface="$(nmcli -g connection.interface-name connection show "$con" 2>/dev/null || true)"
            if [[ "$iface" == "wlan1" ]]; then
                band="$(nmcli -g 802-11-wireless.band connection show "$con" 2>/dev/null || true)"
                if [[ "$band" != "a" ]]; then
                    continue
                fi
                ssid="$(nmcli -g 802-11-wireless.ssid connection show "$con" 2>/dev/null || true)"
                [[ -z "$ssid" ]] && continue
                slug="$(slugify "$ssid")"
                new_name="gate-$slug"
                if [[ -n "${SEEN_SLUGS[$slug]:-}" ]]; then
                    nmcli connection delete "$con"
                    continue
                fi
                SEEN_SLUGS[$slug]=1
                psk="$(nmcli -s -g 802-11-wireless-security.psk connection show "$con" 2>/dev/null || true)"
                key_mgmt="$(nmcli -g 802-11-wireless-security.key-mgmt connection show "$con" 2>/dev/null || true)"
                nmcli connection delete "$con"
                if [[ -n "$psk" ]]; then
                    nmcli connection add type wifi ifname wlan1 con-name "$new_name" ssid "$ssid" \
                        wifi.band a wifi-sec.key-mgmt "$key_mgmt" wifi-sec.psk "$psk" autoconnect yes \
                        ipv4.method auto ipv4.route-metric 100 ipv6.method auto
                else
                    nmcli connection add type wifi ifname wlan1 con-name "$new_name" ssid "$ssid" \
                        wifi.band a autoconnect yes ipv4.method auto ipv4.route-metric 100 \
                        ipv6.method auto
                fi
            fi
        done < <(nmcli -t -f NAME connection show)
        nmcli device connect wlan1 || true
    else
        echo "Warning: device wlan1 not found; skipping wlan1 connection reinstall." >&2
    fi
fi

if [[ $RUN_CONFIGURE_NET == true ]]; then
    if [[ $UNSAFE == false ]]; then
        if [[ -n "$PROTECTED_CONN" && -n "$PROTECTED_DEV" ]]; then
            skip_network_step "NetworkManager connection reconfiguration" "connection '$PROTECTED_CONN' on $PROTECTED_DEV"
        else
            skip_network_step "NetworkManager connection reconfiguration"
        fi
    else
        if ! nmcli -t -f DEVICE device status | grep -Fxq "eth0"; then
            echo "Warning: device eth0 not found; skipping eth0 configuration." >&2
        else
            nmcli device disconnect eth0 >/dev/null 2>&1 || true
            target_conn="$ETH0_CONNECTION_NAME"
            nmcli -t -f NAME,DEVICE connection show | awk -F: -v protect="$PROTECTED_CONN" -v target="$target_conn" '$2=="eth0" && $1!=protect && $1!=target {print $1}' |
                while read -r con; do
                    nmcli connection delete "$con"
                done
            eth0_ip=""
            if [[ "$ETH0_MODE_EFFECTIVE" == "client" ]]; then
                if nmcli -t -f NAME connection show | grep -Fxq "$ETH0_CONNECTION_CLIENT"; then
                    nmcli connection modify "$ETH0_CONNECTION_CLIENT" \
                        connection.interface-name eth0 \
                        ipv4.method auto \
                        ipv4.never-default no \
                        ipv4.route-metric 100 \
                        ipv6.method auto \
                        ipv6.never-default no \
                        connection.autoconnect yes \
                        connection.autoconnect-priority 0
                else
                    nmcli connection add type ethernet ifname eth0 con-name "$ETH0_CONNECTION_CLIENT" autoconnect yes \
                        ipv4.method auto ipv4.never-default no ipv4.route-metric 100 \
                        ipv6.method auto ipv6.never-default no \
                        connection.autoconnect-priority 0
                fi
            else
                eth0_ip="${ETH0_SUBNET_BASE}.10/${ETH0_PREFIX}"
                ETH0_SUBNET_SUFFIX="${ETH0_SUBNET_BASE##*.}"
                if [[ -z "$ETH0_SUBNET_SUFFIX" || "$ETH0_SUBNET_SUFFIX" == "$ETH0_SUBNET_BASE" ]]; then
                    ETH0_SUBNET_SUFFIX="$DEFAULT_ETH0_SUBNET_SUFFIX"
                elif [[ ! "$ETH0_SUBNET_SUFFIX" =~ ^[0-9]+$ ]]; then
                    ETH0_SUBNET_SUFFIX="$DEFAULT_ETH0_SUBNET_SUFFIX"
                fi
                printf -v ETH0_IPV6_SEGMENT "%02x" "$ETH0_SUBNET_SUFFIX"
                ETH0_IPV6_ADDRESS="fd42:${ETH0_IPV6_SEGMENT}::1/64"
                if nmcli -t -f NAME connection show | grep -Fxq "$ETH0_CONNECTION_SHARED"; then
                    nmcli connection modify "$ETH0_CONNECTION_SHARED" \
                        connection.interface-name eth0 \
                        ipv4.method shared \
                        ipv4.addresses "$eth0_ip" \
                        ipv4.never-default yes \
                        ipv4.route-metric 10000 \
                        ipv6.method shared \
                        ipv6.addresses "$ETH0_IPV6_ADDRESS" \
                        ipv6.never-default yes \
                        connection.autoconnect yes \
                        connection.autoconnect-priority 0
                else
                    nmcli connection add type ethernet ifname eth0 con-name "$ETH0_CONNECTION_SHARED" autoconnect yes \
                        ipv4.method shared ipv4.addresses "$eth0_ip" ipv4.never-default yes \
                        ipv4.route-metric 10000 ipv6.method shared ipv6.addresses "$ETH0_IPV6_ADDRESS" ipv6.never-default yes \
                        connection.autoconnect-priority 0
                fi
            fi
            nmcli connection up "$target_conn" >/dev/null 2>&1 || true
            if [[ "$ETH0_MODE_EFFECTIVE" != "client" && $WLAN1_DETECTED == true ]]; then
                ensure_forwarding_rule eth0 wlan1
                ensure_masquerade_rule wlan1
            fi
            if [[ "$ETH0_MODE_EFFECTIVE" == "client" ]]; then
                ensure_dhcp_client_registration eth0
                echo "Configured eth0 for DHCP client mode via NetworkManager profile '$ETH0_CONNECTION_CLIENT'."
            else
                echo "Configured eth0 for DHCP server mode via NetworkManager profile '$ETH0_CONNECTION_SHARED' (${eth0_ip})."
            fi
        fi

        if ! nmcli -t -f DEVICE device status | grep -Fxq "wlan0"; then
            echo "Warning: device wlan0 not found; skipping wlan0 configuration." >&2
        else
            nmcli -t -f NAME,DEVICE connection show | awk -F: -v ap="$AP_NAME" -v hl="$HYPERLINE_NAME" -v protect="$PROTECTED_CONN" '$2=="wlan0" && $1!=ap && $1!=hl && $1!=protect {print $1}' | while read -r con; do
                nmcli connection delete "$con"
            done

            if [[ $AP_HYPERLINE_BY_USER == true ]]; then
                echo "Skipping Hyperline client connection setup because access point name is '$AP_NAME'."
            else
                nmcli connection delete "$HYPERLINE_NAME" 2>/dev/null || true
                nmcli connection add type wifi ifname wlan0 con-name "$HYPERLINE_NAME" \
                    connection.interface-name wlan0 \
                    ssid "Hyperline" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "arthexis" \
                    autoconnect yes connection.autoconnect-priority 20 \
                    ipv4.method auto ipv6.method auto ipv4.route-metric 50

                if [[ $AP_ACTIVATED == true ]]; then
                    echo "Hyperline client connection configured but left inactive while '$AP_NAME' access point is running."
                else
                    if ! nmcli connection up "$HYPERLINE_NAME"; then
                        echo "Failed to activate Hyperline connection; trying existing wlan0 connections." >&2
                        while read -r con; do
                            if nmcli connection up "$con"; then
                                break
                            fi
                        done < <(nmcli -t -f NAME connection show | grep '^gate-')
                    fi
                fi
            fi

            if [[ $AP_ACTIVATED == true ]]; then
                if ! nmcli -t -f NAME connection show --active | grep -Fxq "$AP_NAME"; then
                    if ! nmcli connection up "$AP_NAME" ifname wlan0; then
                        echo "Warning: Unable to reactivate access point '$AP_NAME' after wlan0 client configuration." >&2
                    fi
                fi
            fi
        fi
    fi
fi


if [[ $RUN_ROUTING == true ]]; then
    if [[ $UNSAFE == false ]]; then
        if [[ -n "$PROTECTED_CONN" && -n "$PROTECTED_DEV" ]]; then
            skip_network_step "routing updates" "connection '$PROTECTED_CONN' on $PROTECTED_DEV"
        else
            skip_network_step "routing updates"
        fi
    else
        if [[ "$ETH0_MODE_EFFECTIVE" != "client" ]]; then
            ip route del default dev eth0 2>/dev/null || true
        fi
        ip route del default dev wlan0 2>/dev/null || true

        DEFAULT_ROUTE_SET=false
        if [[ "$ETH0_MODE_EFFECTIVE" == "client" ]]; then
            ETH0_GW=$(discover_gateway_address eth0 || true)
            if [[ -z "$ETH0_GW" && -n "$ETH0_DHCP_SERVER" ]]; then
                echo "Warning: DHCP server $ETH0_DHCP_SERVER is not known to be a gateway; skipping eth0 default route." >&2
            fi
            if [[ -n "$ETH0_GW" ]]; then
                ip route replace default via "$ETH0_GW" dev eth0 2>/dev/null || true
                DEFAULT_ROUTE_SET=true
            else
                echo "Warning: No gateway reported for eth0; falling back to wlan0." >&2
            fi
        fi

        if [[ $DEFAULT_ROUTE_SET == false ]]; then
            WLAN0_GW=$(nmcli -g IP4.GATEWAY device show wlan0 2>/dev/null | head -n1)
            if [[ -n "$WLAN0_GW" ]]; then
                ip route replace default via "$WLAN0_GW" dev wlan0 2>/dev/null || true
            fi
        fi

        exit_code=0
        if check_connectivity; then
            echo "Internet connectivity confirmed."
        else
            if ! nmcli -t -f NAME,DEVICE connection show --active | grep -Fxq "hyperline:wlan0" && \
               nmcli -t -f DEVICE,STATE device status | grep -E '^wlan0:(connecting|connected)' >/dev/null; then
                sleep 10
                if check_connectivity; then
                    echo "Internet connectivity confirmed."
                else
                    echo "No internet connectivity after configuration." >&2
                    exit_code=1
                fi
            else
                echo "No internet connectivity after configuration." >&2
                exit_code=1
            fi
        fi

        nmcli device status
        if [[ $exit_code -ne 0 ]]; then
            exit $exit_code
        fi
    fi
fi

# Restore any previously active connection that was removed
if [[ $NMCLI_AVAILABLE == true ]]; then
    if [[ -n "$PROTECTED_CONN_BACKUP" ]]; then
        if ! nmcli -t -f NAME connection show | grep -Fxq "$PROTECTED_CONN"; then
            nmcli connection clone "$PROTECTED_CONN_BACKUP" "$PROTECTED_CONN" >/dev/null 2>&1 || true
        fi
        nmcli connection delete "$PROTECTED_CONN_BACKUP" >/dev/null 2>&1 || true
    fi

    # Ensure NetworkManager leaves Wi-Fi interfaces in a state where new
    # connections can be discovered after the script completes.
    nmcli radio wifi on >/dev/null 2>&1 || true
    nmcli device set wlan1 managed yes >/dev/null 2>&1 || true
    nmcli device connect wlan1 >/dev/null 2>&1 || true
fi

print_network_status
