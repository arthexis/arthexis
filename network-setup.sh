#!/bin/bash

# Network setup script
# Configures eth0 with a shared static IP, connects wlan1 as the internet uplink,
# and creates a Wi-Fi access point on wlan0 using NetworkManager (nmcli).

set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$BASE_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1
cd "$BASE_DIR"
LOCK_DIR="$BASE_DIR/locks"

usage() {
    cat <<USAGE
Usage: $0 [--password] [--no-firewall] [--unsafe] [--interactive|-i]
  --password      Prompt for a new WiFi password even if one is already configured.
  --no-firewall   Skip firewall port validation.
  --unsafe        Allow modification of the active internet connection.
  --interactive, -i  Collect user decisions for each step before executing.
USAGE
}

FORCE_PASSWORD=false
SKIP_FIREWALL=false
INTERACTIVE=false
UNSAFE=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --password)
            FORCE_PASSWORD=true
            ;;
        --no-firewall)
            SKIP_FIREWALL=true
            ;;
        --unsafe)
            UNSAFE=true
            ;;
        -i|--interactive)
            INTERACTIVE=true
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

# Check initial internet connectivity (non-fatal)
check_connectivity() {
    ping -c1 -W2 8.8.8.8 >/dev/null 2>&1
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

# Detect the active internet connection unless running in unsafe mode
PROTECTED_DEV=""
PROTECTED_CONN=""
if [[ $UNSAFE == false && $INITIAL_CONNECTIVITY == true ]]; then
    PROTECTED_DEV=$(ip route get 8.8.8.8 2>/dev/null | awk '/dev/ {for(i=1;i<=NF;i++) if($i=="dev") {print $(i+1); break}}' || true)
    if [[ -n "$PROTECTED_DEV" ]]; then
        PROTECTED_CONN=$(nmcli -t -f NAME,DEVICE connection show --active | awk -F: -v dev="$PROTECTED_DEV" '$2==dev {print $1; exit}')
        if [[ -n "$PROTECTED_CONN" ]]; then
            echo "Preserving active connection '$PROTECTED_CONN' on '$PROTECTED_DEV'"
        fi
    fi
fi

# Determine access point name and password before running steps
AP_NAME="gelectriic-ap"
EXISTING_PASS="$(nmcli -s -g 802-11-wireless-security.psk connection show "$AP_NAME" 2>/dev/null || true)"
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

# Collect user decisions for each step in advance
ask_step RUN_SERVICES "Ensure required services"
ask_step RUN_AP "Configure wlan0 access point"
ask_step RUN_WLAN1_REFRESH "Install wlan1 device refresh service"
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
    if [[ $UNSAFE == false && "$PROTECTED_DEV" == "wlan0" ]]; then
        echo "Skipping wlan0 access point configuration to preserve '$PROTECTED_CONN'."
    else
        nmcli -t -f NAME,DEVICE connection show | awk -F: -v ap="$AP_NAME" -v protect="$PROTECTED_CONN" '$2=="wlan0" && $1!=ap && $1!=protect {print $1}' | while read -r con; do
            nmcli connection delete "$con"
        done

        if nmcli -t -f NAME connection show | grep -Fxq "$AP_NAME"; then
            nmcli connection modify "$AP_NAME" \
                connection.interface-name wlan0 \
                wifi.ssid "$AP_NAME" \
                wifi.mode ap \
                wifi.band bg \
                wifi-sec.key-mgmt wpa-psk \
                wifi-sec.psk "$WIFI_PASS" \
                ipv4.method shared \
                ipv4.addresses 10.42.0.1/16 \
                ipv4.never-default yes \
                ipv6.method ignore \
                ipv6.never-default yes \
                connection.autoconnect yes
        else
            nmcli connection add type wifi ifname wlan0 con-name "$AP_NAME" autoconnect yes \
                ssid "$AP_NAME" mode ap ipv4.method shared ipv4.addresses 10.42.0.1/16 \
                ipv4.never-default yes ipv6.method ignore ipv6.never-default yes \
                wifi.band bg wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$WIFI_PASS"
        fi
        nmcli connection up eth0-shared || true
        nmcli connection up "$AP_NAME"
        if command -v iptables >/dev/null 2>&1; then
            iptables -C INPUT -i wlan0 -d 10.42.0.1 -j ACCEPT 2>/dev/null || \
                iptables -A INPUT -i wlan0 -d 10.42.0.1 -j ACCEPT
        fi
        if ! nmcli -t -f NAME connection show --active | grep -Fxq "$AP_NAME"; then
            echo "Access point $AP_NAME failed to start." >&2
            exit 1
        fi
    fi
fi

if [[ $RUN_WLAN1_REFRESH == true ]]; then
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
        PORTS+=(8000 8888)
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
    fi
fi

if [[ $RUN_CONFIGURE_NET == true ]]; then
    if [[ $UNSAFE == false && "$PROTECTED_DEV" == "eth0" ]]; then
        echo "Skipping eth0 reconfiguration to preserve '$PROTECTED_CONN'."
    else
        nmcli -t -f NAME,DEVICE connection show | awk -F: -v protect="$PROTECTED_CONN" '$2=="eth0" && $1!=protect {print $1}' | while read -r con; do
            nmcli connection delete "$con"
        done
        nmcli connection add type ethernet ifname eth0 con-name eth0-shared autoconnect yes \
            ipv4.method shared ipv4.addresses 192.168.129.10/16 ipv4.never-default yes \
            ipv4.route-metric 10000 ipv6.method ignore ipv6.never-default yes
    fi

    if [[ $UNSAFE == false && "$PROTECTED_DEV" == "wlan0" ]]; then
        echo "Skipping wlan0 reconfiguration to preserve '$PROTECTED_CONN'."
    else
        nmcli -t -f NAME,DEVICE connection show | awk -F: -v ap="$AP_NAME" -v protect="$PROTECTED_CONN" '$2=="wlan0" && $1!=ap && $1!=protect {print $1}' | while read -r con; do
            nmcli connection delete "$con"
        done
    fi

    if [[ $UNSAFE == false && "$PROTECTED_DEV" == "wlan1" ]]; then
        echo "Skipping wlan1 configuration to preserve '$PROTECTED_CONN'."
    else
        nmcli connection delete hyperline 2>/dev/null || true
        nmcli connection add type wifi ifname wlan1 con-name hyperline \
            ssid "Hyperline" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "arthexis" \
            autoconnect yes ipv4.method auto ipv6.method ignore ipv4.route-metric 100

        if ! nmcli connection up hyperline; then
            echo "Failed to activate Hyperline connection; trying existing wlan1 connections." >&2
            while read -r con; do
                if nmcli connection up "$con"; then
                    break
                fi
            done < <(nmcli -t -f NAME connection show | grep '^gate-')
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

    if [[ $UNSAFE == false && "$PROTECTED_DEV" == "wlan1" ]]; then
        echo "Skipping default route change to preserve '$PROTECTED_CONN'."
    else
        WLAN1_GW=$(nmcli -g IP4.GATEWAY device show wlan1 2>/dev/null | head -n1)
        if [[ -n "$WLAN1_GW" ]]; then
            ip route replace default via "$WLAN1_GW" dev wlan1 2>/dev/null || true
        fi
    fi

    nmcli device status

    if check_connectivity; then
        echo "Internet connectivity confirmed."
    else
        echo "No internet connectivity after configuration." >&2
        exit 1
    fi
fi
