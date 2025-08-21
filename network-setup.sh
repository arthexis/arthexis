#!/bin/bash

# Network setup script
# Configures eth0 with static IP and DHCP, sets up WPA3 access point on wlan0,
# optionally configures firewall rules.

set -euo pipefail

# Defaults
FORCE_PASSWORD=false
ENABLE_FIREWALL=false

usage() {
    cat <<USAGE
Usage: $0 [--password] [--firewall]
  --password   Prompt for a new WiFi password even if one is already configured.
  --firewall   Configure firewall rules and ensure port 8888 is reachable.
USAGE
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --password)
            FORCE_PASSWORD=true
            ;;
        --firewall)
            ENABLE_FIREWALL=true
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

ETH_IP="192.168.129.10/16"
WLAN_IP="192.168.129.1/16"
DNSMASQ_CONF="/etc/dnsmasq.d/network-setup.conf"
HOSTAPD_CONF="/etc/hostapd/hostapd-network-setup.conf"
SYSCTL_CONF="/etc/sysctl.d/99-network-setup.conf"

# Ensure required packages are installed
need_pkg() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "Installing $1..."
        apt-get update && apt-get install -y "$1"
    }
}

need_pkg dnsmasq
need_pkg hostapd
need_pkg iptables
need_pkg iproute2

# Configure sysctl for IP forwarding
if [[ ! -f $SYSCTL_CONF ]] || ! grep -q '^net.ipv4.ip_forward=1$' "$SYSCTL_CONF"; then
    echo 'net.ipv4.ip_forward=1' > "$SYSCTL_CONF"
fi
sysctl -w net.ipv4.ip_forward=1 >/dev/null

# Configure eth0 IP
current_eth_ip=$(ip -4 addr show dev eth0 | awk '/inet /{print $2}')
if [[ "$current_eth_ip" != "$ETH_IP" ]]; then
    ip addr flush dev eth0
    ip addr add "$ETH_IP" dev eth0
fi
ip link set eth0 up

# Configure wlan0 IP
current_wlan_ip=$(ip -4 addr show dev wlan0 | awk '/inet /{print $2}')
if [[ "$current_wlan_ip" != "$WLAN_IP" ]]; then
    ip addr flush dev wlan0 || true
    ip addr add "$WLAN_IP" dev wlan0
fi
ip link set wlan0 up

# Prevent default route via eth0
if ip route show default dev eth0 >/dev/null 2>&1; then
    ip route del default dev eth0 || true
fi

# Determine upstream interface for NAT
UPSTREAM=$(ip route show default | awk '{print $5}' | head -n1)
if [[ -n "$UPSTREAM" && "$UPSTREAM" != "eth0" && "$UPSTREAM" != "wlan0" ]]; then
    iptables -t nat -C POSTROUTING -o "$UPSTREAM" -j MASQUERADE 2>/dev/null || \
        iptables -t nat -A POSTROUTING -o "$UPSTREAM" -j MASQUERADE
    for IFACE in eth0 wlan0; do
        iptables -C FORWARD -i "$IFACE" -o "$UPSTREAM" -j ACCEPT 2>/dev/null || \
            iptables -A FORWARD -i "$IFACE" -o "$UPSTREAM" -j ACCEPT
        iptables -C FORWARD -i "$UPSTREAM" -o "$IFACE" -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || \
            iptables -A FORWARD -i "$UPSTREAM" -o "$IFACE" -m state --state RELATED,ESTABLISHED -j ACCEPT
    done
fi

# Configure dnsmasq
mkdir -p /etc/dnsmasq.d
cat > "$DNSMASQ_CONF" <<DNS
interface=eth0
interface=wlan0
dhcp-range=192.168.0.50,192.168.255.200,12h
DNS

systemctl restart dnsmasq 2>/dev/null || service dnsmasq restart 2>/dev/null || true

# Obtain or prompt for WiFi password
if [[ ! -f $HOSTAPD_CONF || $FORCE_PASSWORD == true ]]; then
    while true; do
        read -rsp "Enter WiFi password for 'Gelectriic AP': " WIFI_PASS1; echo
        read -rsp "Confirm password: " WIFI_PASS2; echo
        if [[ "$WIFI_PASS1" == "$WIFI_PASS2" && -n "$WIFI_PASS1" ]]; then
            break
        else
            echo "Passwords do not match or are empty." >&2
        fi
    done
else
    WIFI_PASS1=$(grep '^wpa_passphrase=' "$HOSTAPD_CONF" | cut -d= -f2-)
fi

mkdir -p /etc/hostapd
cat > "$HOSTAPD_CONF" <<HOSTAPD
interface=wlan0
ssid=Gelectriic AP
hw_mode=g
channel=6
wpa=2
wpa_key_mgmt=SAE
rsn_pairwise=CCMP
ieee80211w=2
wpa_passphrase=$WIFI_PASS1
HOSTAPD

systemctl unmask hostapd 2>/dev/null || true
systemctl enable hostapd 2>/dev/null || true
systemctl restart hostapd 2>/dev/null || service hostapd restart 2>/dev/null || true

# Firewall rules
if [[ $ENABLE_FIREWALL == true ]]; then
    for IFACE in eth0 wlan0; do
        iptables -C INPUT -i "$IFACE" -p tcp --dport 22 -j ACCEPT 2>/dev/null || \
            iptables -A INPUT -i "$IFACE" -p tcp --dport 22 -j ACCEPT
        iptables -C INPUT -i "$IFACE" -p tcp --dport 8888 -j ACCEPT 2>/dev/null || \
            iptables -A INPUT -i "$IFACE" -p tcp --dport 8888 -j ACCEPT
    done
    iptables -C INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || \
        iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
    iptables -C INPUT -i lo -j ACCEPT 2>/dev/null || iptables -A INPUT -i lo -j ACCEPT

    if command -v nc >/dev/null 2>&1; then
        if nc -z localhost 8888 >/dev/null 2>&1; then
            echo "Port 8888 is reachable."
        else
            echo "Port 8888 is not responding; ensure a service is listening." >&2
        fi
    else
        echo "nc command not found; skipping port 8888 reachability test." >&2
    fi
fi

echo "Network setup complete."
