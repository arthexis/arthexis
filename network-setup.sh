#!/bin/bash

# Network setup script
# Configures eth0 with a shared static IP and creates a Wi-Fi access point on
# wlan0 using NetworkManager (nmcli).

set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"
LOCK_DIR="$BASE_DIR/locks"

usage() {
    cat <<USAGE
Usage: $0 [--password] [--no-firewall]
  --password     Prompt for a new WiFi password even if one is already configured.
  --no-firewall  Skip firewall port validation.
USAGE
}

FORCE_PASSWORD=false
SKIP_FIREWALL=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --password)
            FORCE_PASSWORD=true
            ;;
        --no-firewall)
            SKIP_FIREWALL=true
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

command -v nmcli >/dev/null 2>&1 || {
    echo "nmcli (NetworkManager) is required." >&2
    exit 1
}

if [[ $SKIP_FIREWALL == false ]]; then
    PORTS=(22 5900 21114)
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

slugify() {
    local input="$1"
    echo "$input" | tr '[:upper:]' '[:lower:]' | sed -e 's/[^a-z0-9]/-/g' -e 's/--*/-/g' -e 's/^-//' -e 's/-$//'
}

# Reinstall wlan1 connections with uniform naming, only for 5GHz networks
if nmcli -t -f DEVICE device status | grep -Fxq "wlan1"; then
    declare -A SEEN_SLUGS=()
    nmcli device disconnect wlan1 || true
    while IFS= read -r con; do
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
                    wifi.band a wifi-sec.key-mgmt "$key_mgmt" wifi-sec.psk "$psk" autoconnect yes
            else
                nmcli connection add type wifi ifname wlan1 con-name "$new_name" ssid "$ssid" \
                    wifi.band a autoconnect yes
            fi
        fi
    done < <(nmcli -t -f NAME connection show)
    nmcli device connect wlan1 || true
fi

# Preserve existing password if connection already exists
EXISTING_PASS="$(nmcli -s -g 802-11-wireless-security.psk connection show gelectriic-ap 2>/dev/null || true)"

# Remove existing connections on eth0 and wlan0
for dev in eth0 wlan0; do
    nmcli -t -f NAME,DEVICE connection show | awk -F: -v D="$dev" '$2==D {print $1}' | while read -r con; do
        nmcli connection delete "$con"
    done
done

# Configure eth0 shared connection
nmcli connection add type ethernet ifname eth0 con-name eth0-shared autoconnect yes \
    ipv4.method shared ipv4.addresses 192.168.129.10/16 ipv4.never-default yes \
    ipv6.method ignore ipv6.never-default yes

# Obtain or prompt for WiFi password
if [[ -z "$EXISTING_PASS" || $FORCE_PASSWORD == true ]]; then
    while true; do
        read -rsp "Enter WiFi password for 'gelectriic-ap': " WIFI_PASS1; echo
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

# Configure wlan0 access point
nmcli connection add type wifi ifname wlan0 con-name gelectriic-ap autoconnect yes \
    ssid gelectriic-ap mode ap ipv4.method shared ipv4.addresses 10.42.0.1/16 \
    ipv4.never-default yes ipv6.method ignore ipv6.never-default yes \
    wifi.band bg wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$WIFI_PASS"

# Bring up connections
nmcli connection up eth0-shared
nmcli connection up gelectriic-ap

# Show final status
nmcli device status

# Check internet connectivity
if ping -c1 -W2 8.8.8.8 >/dev/null 2>&1; then
    echo "Internet connectivity confirmed."
else
    echo "No internet connectivity." >&2
fi
