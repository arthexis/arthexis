#!/bin/bash

# Network setup script
# Configures eth0 with a shared static IP and sets up both the Hyperline
# client and the gelectriic access point on wlan0 using NetworkManager (nmcli).

set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/helpers/logging.sh
. "$BASE_DIR/scripts/helpers/logging.sh"
arthexis_resolve_log_dir "$BASE_DIR" LOG_DIR || exit 1
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1
cd "$BASE_DIR"
LOCK_DIR="$BASE_DIR/locks"
PUBLIC_WIFI_LOCK="$LOCK_DIR/public_wifi_mode.lck"
PUBLIC_WIFI_ALLOW="$LOCK_DIR/public_wifi_allow.list"
mkdir -p "$LOCK_DIR"

usage() {
    cat <<USAGE
Usage: $0 [--password] [--ap NAME] [--no-firewall] [--unsafe] [--public] [--interactive|-i] [--no-watchdog] [--vnc] [--no-vnc] [--subnet N[/P]]
  --password      Prompt for a new WiFi password even if one is already configured.
  --ap NAME       Set the wlan0 access point name (SSID) to NAME.
  --no-firewall   Skip firewall port validation.
  --unsafe        Allow modification of the active internet connection.
  --public        Configure an open access point without internet sharing.
  --interactive, -i  Collect user decisions for each step before executing.
  --no-watchdog   Skip installing the WiFi watchdog service.
  --vnc           Require validating that a VNC service is enabled.
  --no-vnc        Skip validating that a VNC service is enabled (default).
  --subnet N[/P]  Configure eth0 on the 192.168.N.0/P subnet (default: 129/16).
                  Accepts prefix lengths of 16 or 24.
USAGE
}

FORCE_PASSWORD=false
SKIP_FIREWALL=false
INTERACTIVE=false
UNSAFE=false
PUBLIC_MODE=false
INSTALL_WATCHDOG=true
REQUIRE_VNC=false
VNC_OPTION_SET=false
DEFAULT_AP_NAME="gelectriic-ap"
AP_NAME="$DEFAULT_AP_NAME"
AP_SPECIFIED=false
AP_NAME_LOWER=""
SKIP_AP=false
ETH0_SUBNET=129
ETH0_PREFIX=16
validate_subnet_value() {
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
            echo "Error: --subnet requires a value in the form N or N/P." >&2
            exit 1
        fi
    fi
    validate_subnet_value "$subnet"
    validate_prefix_value "$prefix"
    ETH0_SUBNET="$subnet"
    ETH0_PREFIX="$prefix"
}
while [[ $# -gt 0 ]]; do
    case "$1" in
        --password)
            FORCE_PASSWORD=true
            ;;
        --ap)
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
            shift
            ;;
        --ap=*)
            AP_NAME="${1#--ap=}"
            if [[ -z "$AP_NAME" ]]; then
                echo "Error: --ap requires a non-empty name." >&2
                exit 1
            fi
            AP_SPECIFIED=true
            ;;
        --subnet)
            if [[ $# -lt 2 ]]; then
                echo "Error: --subnet requires a value." >&2
                exit 1
            fi
            set_subnet_and_prefix "$2"
            shift
            ;;
        --subnet=*)
            subnet_value="${1#--subnet=}"
            set_subnet_and_prefix "$subnet_value"
            ;;
        --no-firewall)
            SKIP_FIREWALL=true
            ;;
        --no-ap)
            SKIP_AP=true
            ;;
        --unsafe)
            UNSAFE=true
            ;;
        --public)
            PUBLIC_MODE=true
            ;;
        -i|--interactive)
            INTERACTIVE=true
            ;;
        --no-watchdog)
            INSTALL_WATCHDOG=false
            ;;
        --vnc)
            REQUIRE_VNC=true
            VNC_OPTION_SET=true
            ;;
        --no-vnc)
            REQUIRE_VNC=false
            VNC_OPTION_SET=true
            ;;
        -h|--help)
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

if [[ $PUBLIC_MODE == true && $FORCE_PASSWORD == true ]]; then
    echo "Warning: --password ignored in public mode." >&2
    FORCE_PASSWORD=false
fi

if [[ $EUID -ne 0 ]]; then
    echo "This script must be run as root" >&2
    exit 1
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

# Slugify helper used for connection names
slugify() {
    local input="$1"
    echo "$input" | tr '[:upper:]' '[:lower:]' | sed -e 's/[^a-z0-9]/-/g' -e 's/--*/-/g' -e 's/^-//' -e 's/-$//'
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
    if [[ $PUBLIC_MODE == true ]]; then
        WIFI_PASS=""
    else
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
fi

# Collect user decisions for each step in advance
ask_step RUN_SERVICES "Ensure required services"
if [[ $SKIP_AP == true ]]; then
    RUN_AP=false
else
    ask_step RUN_AP "Configure wlan0 access point"
fi
ask_step RUN_WLAN1_REFRESH "Install wlan1 device refresh service"
if [[ $INSTALL_WATCHDOG == true ]]; then
    ask_step RUN_WIFI_WATCHDOG "Install WiFi watchdog service"
else
    RUN_WIFI_WATCHDOG=false
fi
ask_step RUN_PACKAGES "Ensure required packages and SSH service"
if [[ $SKIP_FIREWALL == false ]]; then
    ask_step RUN_FIREWALL "Validate firewall ports"
else
    RUN_FIREWALL=false
fi
ask_step RUN_REINSTALL_WLAN1 "Reinstall wlan1 connections"
ask_step RUN_CONFIGURE_NET "Configure network connections"
ask_step RUN_ROUTING "Finalize routing and connectivity checks"

# Execute steps based on user choices
if [[ $RUN_SERVICES == true ]]; then
    ensure_service dbus
    ensure_service NetworkManager
fi

if [[ $RUN_AP == true ]]; then
    if ! nmcli -t -f DEVICE device status | grep -Fxq "wlan0"; then
        echo "Warning: device wlan0 not found; skipping access point configuration." >&2
    elif [[ $UNSAFE == false && "$PROTECTED_DEV" == "wlan0" ]]; then
        echo "Skipping wlan0 access point configuration to preserve '$PROTECTED_CONN'."
    else
        nmcli -t -f NAME,DEVICE connection show | awk -F: -v ap="$AP_NAME" -v hl="$HYPERLINE_NAME" -v protect="$PROTECTED_CONN" '$2=="wlan0" && $1!=ap && $1!=hl && $1!=protect {print $1}' | while read -r con; do
            nmcli connection delete "$con"
        done

        security_args=(wifi-sec.key-mgmt none)
        if [[ $PUBLIC_MODE == false ]]; then
            security_args=(wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$WIFI_PASS")
        fi

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
                ipv6.method ignore \
                ipv6.never-default yes \
                connection.autoconnect yes \
                connection.autoconnect-priority 0
            if [[ $PUBLIC_MODE == true ]]; then
                nmcli connection modify "$AP_NAME" -wifi-sec.psk >/dev/null 2>&1 || true
            fi
        else
            nmcli connection add type wifi ifname wlan0 con-name "$AP_NAME" \
                connection.interface-name wlan0 autoconnect yes connection.autoconnect-priority 0 \
                ssid "$AP_NAME" mode ap ipv4.method shared ipv4.addresses 10.42.0.1/16 \
                ipv4.never-default yes ipv6.method ignore ipv6.never-default yes \
                wifi.band bg "${security_args[@]}"
        fi
        if [[ $PUBLIC_MODE == false ]]; then
            nmcli connection up eth0-shared || true
        else
            nmcli connection down eth0-shared >/dev/null 2>&1 || true
        fi
        nmcli connection up "$AP_NAME"
        if command -v iptables >/dev/null 2>&1; then
            iptables -C INPUT -i wlan0 -d 10.42.0.1 -j ACCEPT 2>/dev/null || \
                iptables -A INPUT -i wlan0 -d 10.42.0.1 -j ACCEPT
            if [[ $PUBLIC_MODE == true ]]; then
                iptables -C FORWARD -i wlan0 -j DROP 2>/dev/null || \
                    iptables -I FORWARD 1 -i wlan0 -j DROP
                touch "$PUBLIC_WIFI_LOCK"
                if [[ -f "$PUBLIC_WIFI_ALLOW" ]]; then
                    while IFS= read -r mac; do
                        [[ -z "$mac" ]] && continue
                        iptables -C FORWARD -i wlan0 -m mac --mac-source "$mac" -j ACCEPT 2>/dev/null || \
                            iptables -I FORWARD 1 -i wlan0 -m mac --mac-source "$mac" -j ACCEPT
                    done < "$PUBLIC_WIFI_ALLOW"
                fi
            else
                while iptables -C FORWARD -i wlan0 -j DROP 2>/dev/null; do
                    iptables -D FORWARD -i wlan0 -j DROP
                done
                rm -f "$PUBLIC_WIFI_LOCK"
                if [[ -f "$PUBLIC_WIFI_ALLOW" ]]; then
                    while IFS= read -r mac; do
                        [[ -z "$mac" ]] && continue
                        while iptables -C FORWARD -i wlan0 -m mac --mac-source "$mac" -j ACCEPT 2>/dev/null; do
                            iptables -D FORWARD -i wlan0 -m mac --mac-source "$mac" -j ACCEPT || break
                        done
                    done < "$PUBLIC_WIFI_ALLOW"
                fi
            fi
        else
            if [[ $PUBLIC_MODE == true ]]; then
                touch "$PUBLIC_WIFI_LOCK"
            else
                rm -f "$PUBLIC_WIFI_LOCK"
            fi
        fi
        if ! nmcli -t -f NAME connection show --active | grep -Fxq "$AP_NAME"; then
            echo "Access point $AP_NAME failed to start." >&2
            exit 1
        fi
    fi
fi

if [[ $RUN_WLAN1_REFRESH == true ]]; then
    if ! nmcli -t -f DEVICE device status | grep -Fxq "wlan1"; then
        echo "Warning: device wlan1 not found; skipping wlan1 refresh service." >&2
    else
        WLAN1_REFRESH_SCRIPT="$BASE_DIR/scripts/wlan1-refresh.sh"
        WLAN1_REFRESH_SERVICE="wlan1-refresh"
        WLAN1_REFRESH_SERVICE_FILE="/etc/systemd/system/${WLAN1_REFRESH_SERVICE}.service"
        if [ -f "$WLAN1_REFRESH_SCRIPT" ]; then
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
        fi
    fi
fi

if [[ $RUN_WIFI_WATCHDOG == true ]]; then
    WATCHDOG_SCRIPT="$BASE_DIR/scripts/wifi-watchdog.sh"
    WATCHDOG_SERVICE="wifi-watchdog"
    WATCHDOG_SERVICE_FILE="/etc/systemd/system/${WATCHDOG_SERVICE}.service"
    if [ -f "$WATCHDOG_SCRIPT" ]; then
        cat > "$WATCHDOG_SERVICE_FILE" <<EOF
[Unit]
Description=WiFi connectivity watchdog
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$WATCHDOG_SCRIPT
Restart=always

[Install]
WantedBy=multi-user.target
EOF
        systemctl daemon-reload
        systemctl enable "$WATCHDOG_SERVICE" >/dev/null 2>&1 || true
        systemctl restart "$WATCHDOG_SERVICE" || true
    fi
else
    WATCHDOG_SERVICE="wifi-watchdog"
    if systemctl list-unit-files | grep -Fq "${WATCHDOG_SERVICE}.service"; then
        systemctl stop "$WATCHDOG_SERVICE" || true
        systemctl disable "$WATCHDOG_SERVICE" || true
        rm -f "/etc/systemd/system/${WATCHDOG_SERVICE}.service"
        systemctl daemon-reload
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
    ensure_service ssh

    if command -v ufw >/dev/null 2>&1; then
        STATUS=$(ufw status 2>/dev/null || true)
        if ! echo "$STATUS" | grep -iq "inactive"; then
            ufw allow 22/tcp || true
        fi
    fi
fi

if [[ $RUN_FIREWALL == true ]]; then
    PORTS=(22 21114)
    MODE="internal"
    if [ -f "$LOCK_DIR/nginx_mode.lck" ]; then
        MODE="$(cat "$LOCK_DIR/nginx_mode.lck")"
    fi
    if [ "$MODE" = "public" ]; then
        PORTS+=(80 443 8000)
    else
        PORTS+=(8000 8080 8888)
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
    if [[ $UNSAFE == false && "$PROTECTED_DEV" == "wlan1" ]]; then
        echo "Skipping wlan1 connection reinstall to preserve '$PROTECTED_CONN'."
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
                        ipv4.method auto ipv4.route-metric 100 ipv6.method ignore
                else
                    nmcli connection add type wifi ifname wlan1 con-name "$new_name" ssid "$ssid" \
                        wifi.band a autoconnect yes ipv4.method auto ipv4.route-metric 100 \
                        ipv6.method ignore
                fi
            fi
        done < <(nmcli -t -f NAME connection show)
        nmcli device connect wlan1 || true
    else
        echo "Warning: device wlan1 not found; skipping wlan1 connection reinstall." >&2
    fi
fi

if [[ $RUN_CONFIGURE_NET == true ]]; then
    if [[ $UNSAFE == false && "$PROTECTED_DEV" == "eth0" ]]; then
        echo "Skipping eth0 reconfiguration to preserve '$PROTECTED_CONN'."
    elif ! nmcli -t -f DEVICE device status | grep -Fxq "eth0"; then
        echo "Warning: device eth0 not found; skipping eth0 configuration." >&2
    else
        nmcli device disconnect eth0 >/dev/null 2>&1 || true
        nmcli -t -f NAME,DEVICE connection show | awk -F: -v protect="$PROTECTED_CONN" '$2=="eth0" && $1!=protect {print $1}' |
            while read -r con; do
                nmcli connection delete "$con"
            done
        eth0_ip="192.168.${ETH0_SUBNET}.10/${ETH0_PREFIX}"
        if nmcli -t -f NAME connection show | grep -Fxq "eth0-shared"; then
            nmcli connection modify eth0-shared \
                connection.interface-name eth0 \
                ipv4.method shared \
                ipv4.addresses "$eth0_ip" \
                ipv4.never-default yes \
                ipv4.route-metric 10000 \
                ipv6.method ignore \
                ipv6.never-default yes \
                connection.autoconnect yes \
                connection.autoconnect-priority 0
        else
            nmcli connection add type ethernet ifname eth0 con-name eth0-shared autoconnect yes \
                ipv4.method shared ipv4.addresses "$eth0_ip" ipv4.never-default yes \
                ipv4.route-metric 10000 ipv6.method ignore ipv6-never-default yes \
                connection.autoconnect-priority 0
        fi
        nmcli connection up eth0-shared >/dev/null 2>&1 || true
    fi

    if [[ $UNSAFE == false && "$PROTECTED_DEV" == "wlan0" ]]; then
        echo "Skipping wlan0 reconfiguration to preserve '$PROTECTED_CONN'."
    elif ! nmcli -t -f DEVICE device status | grep -Fxq "wlan0"; then
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
                ipv4.method auto ipv6.method ignore ipv4.route-metric 50

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
fi


if [[ $RUN_ROUTING == true ]]; then
    if [[ $UNSAFE == false && "$PROTECTED_DEV" == "eth0" ]]; then
        :
    else
        ip route del default dev eth0 2>/dev/null || true
    fi
    if [[ $UNSAFE == false && "$PROTECTED_DEV" == "wlan0" ]]; then
        :
    else
        ip route del default dev wlan0 2>/dev/null || true
    fi

    if [[ $UNSAFE == false && "$PROTECTED_DEV" == "wlan0" ]]; then
        echo "Skipping default route change to preserve '$PROTECTED_CONN'."
    else
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

# Restore any previously active connection that was removed
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
