#!/usr/bin/env bash
# setup-network.sh
# Prepare Pi networking:
# - wlan0 AP "Gelectriic-HS" (password requested at runtime) with NAT (ipv4.shared)
# - wlan1 preferred upstream gateway (low metric) if present, or enforce later via dispatcher
# - eth0 static 192.168.129.10/24, no gateway, never default

set -euo pipefail

# ----- Safety / prerequisites -----
if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Please run as root (sudo)." >&2
  exit 1
fi

need_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "Missing: $1"; exit 1; }; }
need_cmd nmcli
need_cmd systemctl
need_cmd sed
need_cmd awk

SSID="Gelectriic-HS"
read -rsp "Enter password for AP '${SSID}': " PSK
echo
AP_CON="AP ${SSID}" 
ETH_CON="eth0-static-129"
WLAN1_METRIC="100"
ETH_METRIC="700"
DISPATCHER="/etc/NetworkManager/dispatcher.d/20-prefer-wlan1"

echo "==> Ensuring NetworkManager is installed/active and managing devices…"
# Optional: ensure NM manages interfaces on Debian/Raspberry Pi
mkdir -p /etc/NetworkManager/conf.d
if ! grep -qs 'managed=true' /etc/NetworkManager/NetworkManager.conf /etc/NetworkManager/conf.d/* 2>/dev/null; then
  cat >/etc/NetworkManager/conf.d/10-managed.conf <<'EOF'
[ifupdown]
managed=true

[keyfile]
unmanaged-devices=none
EOF
fi

# Stop dhcpcd if present to avoid conflicts
if systemctl is-enabled --quiet dhcpcd 2>/dev/null || systemctl is-active --quiet dhcpcd 2>/dev/null; then
  systemctl disable --now dhcpcd || true
fi
# (Optional) some images enable a wpa_supplicant service; NM will spawn its own
systemctl disable --now wpa_supplicant 2>/dev/null || true

systemctl enable --now NetworkManager
nmcli general reload || true

echo "==> Unblocking Wi-Fi (rfkill)…"
if command -v rfkill >/dev/null 2>&1; then
  rfkill unblock wifi || true
fi

# ----- ETH0: static LAN, never default -----
echo "==> Configuring eth0 as static LAN without gateway…"
if nmcli -t -f NAME con show | grep -qx "$ETH_CON"; then
  nmcli con mod "$ETH_CON" \
    ipv4.method manual ipv4.addresses "192.168.129.10/24" \
    ipv4.gateway "" ipv4.dns "" ipv4.never-default yes \
    ipv4.route-metric "$ETH_METRIC" \
    ipv6.method disabled
else
  nmcli con add type ethernet ifname eth0 con-name "$ETH_CON" \
    ipv4.method manual ipv4.addresses "192.168.129.10/24" \
    ipv4.gateway "" ipv4.dns "" ipv4.never-default yes \
    ipv4.route-metric "$ETH_METRIC" \
    ipv6.method disabled
fi
nmcli con up "$ETH_CON" || true

# Ensure any other ethernet profiles never become default/DNS
while read -r cname; do
  [ -z "$cname" ] && continue
  [ "$cname" = "$ETH_CON" ] && continue
  nmcli con mod "$cname" ipv4.never-default yes ipv6.never-default yes ipv4.ignore-auto-dns yes || true
done < <(nmcli -t -f NAME,TYPE con show | awk -F: '$2=="802-3-ethernet"{print $1}')

# ----- WLAN0: AP with shared IPv4 (DHCP/NAT provided by NM) -----
echo "==> Creating/Updating hotspot on wlan0 (${SSID})…"
if nmcli -t -f NAME con show | grep -qx "$AP_CON"; then
  nmcli con mod "$AP_CON" \
    connection.interface-name wlan0 \
    802-11-wireless.mode ap 802-11-wireless.ssid "$SSID" \
    802-11-wireless.band bg \
    wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PSK" \
    ipv4.method shared ipv6.method disabled \
    connection.autoconnect yes connection.autoconnect-priority 10
else
  nmcli con add type wifi ifname wlan0 con-name "$AP_CON" ssid "$SSID"
  nmcli con mod "$AP_CON" \
    connection.interface-name wlan0 \
    802-11-wireless.mode ap 802-11-wireless.band bg \
    wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PSK" \
    ipv4.method shared ipv6.method disabled \
    connection.autoconnect yes connection.autoconnect-priority 10
fi
# Bring AP up (ignore if temporarily busy)
nmcli con up "$AP_CON" || true

# ----- WLAN1: prefer as default when present -----
echo "==> Checking for WLAN profiles to prefer on wlan1…"
# First try connections already bound to wlan1
mapfile -t WLAN1_CONNS < <(nmcli -t -f NAME,DEVICE con show | awk -F: '$2=="wlan1"{print $1}')

# If none, take all Wi-Fi profiles except the AP and bind them to wlan1
if [ "${#WLAN1_CONNS[@]}" -eq 0 ]; then
  mapfile -t WLAN1_CONNS < <(nmcli -t -f NAME,TYPE con show | awk -F: '$2=="802-11-wireless"{print $1}')
  # remove the AP profile from list if present
  WLAN1_CONNS=("${WLAN1_CONNS[@]/$AP_CON}")
fi

if [ "${#WLAN1_CONNS[@]}" -gt 0 ]; then
  for c in "${WLAN1_CONNS[@]}"; do
    [ -z "$c" ] && continue
    echo "   -> Updating $c (bind to wlan1, metric $WLAN1_METRIC, allow default)…"
    nmcli con mod "$c" \
      connection.interface-name wlan1 \
      ipv4.never-default no ipv6.never-default yes \
      ipv4.route-metric "$WLAN1_METRIC" ipv6.route-metric "$WLAN1_METRIC" \
      connection.autoconnect yes connection.autoconnect-priority 100 || true
    # Bounce to apply metrics if active
    if nmcli -t -f NAME con show --active | grep -qx "$c"; then
      nmcli con down "$c" || true
      nmcli con up "$c" || true
    fi
  done
else
  echo "==> No saved Wi-Fi profiles to bind to wlan1. Installing a dispatcher to prefer wlan1 when you add one…"
  install -d -m 755 "$(dirname "$DISPATCHER")"
  cat >"$DISPATCHER" <<'EOF'
#!/usr/bin/env bash
# Prefer wlan1 as default route whenever a connection on it comes up.
# - Lowers route metric on the active connection for wlan1
# - Raises/blocks default on others (eth0 remains never-default)
# Triggered by NetworkManager on connection events.

IFACE="$1"; STATE="$2"
[ "$IFACE" = "wlan1" ] || exit 0
[ "$STATE" = "up" ] || exit 0

# Find the active connection name for wlan1
CONN="$(nmcli -t -f NAME,DEVICE con show --active | awk -F: '$2=="wlan1"{print $1}')"
[ -n "$CONN" ] || exit 0

# Make wlan1 preferred
nmcli con mod "$CONN" ipv4.never-default no ipv6.never-default yes ipv4.route-metric 100 ipv6.route-metric 100 2>/dev/null || true

# Ensure eth0 stays LAN-only
for C in $(nmcli -t -f NAME,TYPE con show | awk -F: '$2=="802-3-ethernet"{print $1}'); do
  nmcli con mod "$C" ipv4.never-default yes ipv6.never-default yes ipv4.ignore-auto-dns yes 2>/dev/null || true
done

# Nudge routing: drop any stray default on eth0 right now
ip route del default dev eth0 2>/dev/null || true
exit 0
EOF
  chmod +x "$DISPATCHER"
fi

echo "==> Cleaning up any stray default via eth0 (runtime only)…"
ip route del default dev eth0 2>/dev/null || true

echo "==> Restarting NetworkManager to settle…"
systemctl restart NetworkManager || true
sleep 2

echo "==> Final state:"
nmcli dev status || true
echo "--- routes ---"
ip route || true
echo "--- DNS ---"
if command -v resolvectl >/dev/null 2>&1; then
  resolvectl status | sed -n '1,150p' || true
elif command -v systemd-resolve >/dev/null 2>&1; then
  systemd-resolve --status | sed -n '1,150p' || true
else
  echo "No resolvectl/systemd-resolve found." || true
fi

echo
echo "Done. Notes:"
echo " - AP '${SSID}' should be broadcasting on wlan0; clients get 10.42.0.0/24 and NAT through the current default."
echo " - eth0 is 192.168.129.10/24 with NO gateway (never default)."
echo " - Any connection you add on wlan1 will be preferred as the default route (metric ${WLAN1_METRIC})."
