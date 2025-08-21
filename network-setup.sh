#!/bin/bash

# Network setup script
# Configures eth0 with a static IP and creates a Wi-Fi access point on wlan0
# using NetworkManager (nmcli).

set -euo pipefail

usage() {
    cat <<USAGE
Usage: $0 [--password]
  --password  Prompt for a new WiFi password even if one is already configured.
USAGE
}

FORCE_PASSWORD=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --password)
            FORCE_PASSWORD=true
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

# Preserve existing password if connection already exists
EXISTING_PASS="$(nmcli -s -g 802-11-wireless-security.psk connection show gelectriic-ap 2>/dev/null || true)"

# Remove existing connections on eth0 and wlan0
for dev in eth0 wlan0; do
    nmcli -t -f NAME,DEVICE connection show | awk -F: -v D="$dev" '$2==D {print $1}' | while read -r con; do
        nmcli connection delete "$con"
    done
done

# Configure eth0 static connection
nmcli connection add type ethernet ifname eth0 con-name eth0-static autoconnect yes \
    ipv4.method manual ipv4.addresses 192.168.129.10/16 ipv4.never-default yes ipv6.method ignore

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
    wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$WIFI_PASS"

# Bring up connections
nmcli connection up eth0-static
nmcli connection up gelectriic-ap

# Show final status
nmcli device status

# Check internet connectivity
if ping -c1 -W2 8.8.8.8 >/dev/null 2>&1; then
    echo "Internet connectivity confirmed."
else
    echo "No internet connectivity." >&2
fi
