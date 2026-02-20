#!/usr/bin/env bash
set -euo pipefail

# Reconfigure NetworkManager connection profiles so wlan0 is used for upstream
# internet and wlan1 is reserved for access-point mode when a USB dongle exists.

readonly INTERNET_IFACE="wlan0"
readonly AP_IFACE="wlan1"

log() {
  printf '[nmcli-setup] %s\n' "$*"
}

require_nmcli() {
  if ! command -v nmcli >/dev/null 2>&1; then
    printf 'nmcli-setup.sh requires nmcli in PATH\n' >&2
    exit 1
  fi
}

clear_wifi_mac_pins() {
  local connection_id="$1"
  nmcli connection modify "$connection_id" \
    802-11-wireless.mac-address "" \
    802-11-wireless.cloned-mac-address "" || return
}

configure_wifi_profile() {
  local connection_id="$1"
  local mode iface
  local -a props

  mapfile -t props < <(LC_ALL=C nmcli --get-values 802-11-wireless.mode,connection.interface-name connection show "$connection_id") || return
  mode="${props[0]:-}"
  iface="${props[1]:-}"

  clear_wifi_mac_pins "$connection_id"

  if [[ "$mode" == "ap" ]]; then
    nmcli connection modify "$connection_id" \
      connection.interface-name "$AP_IFACE" \
      connection.autoconnect yes \
      ipv4.method shared || return
    log "AP profile '$connection_id' pinned to $AP_IFACE."
    return
  fi

  if [[ "$iface" == "$AP_IFACE" || "$iface" == "$INTERNET_IFACE" || -z "$iface" ]]; then
    nmcli connection modify "$connection_id" \
      connection.interface-name "$INTERNET_IFACE" \
      connection.autoconnect yes || return
    log "Wi-Fi client profile '$connection_id' pinned to $INTERNET_IFACE."
  else
    log "Wi-Fi client profile '$connection_id' pinned to '$iface'; leaving interface-name unchanged."
  fi
}

configure_ethernet_profile() {
  local connection_id="$1"
  local ipv4_method

  ipv4_method="$(LC_ALL=C nmcli --get-values ipv4.method connection show "$connection_id" | head -n1)" || return

  if [[ "$ipv4_method" == "shared" ]]; then
    nmcli connection modify "$connection_id" \
      ipv4.never-default yes \
      ipv6.never-default yes \
      ipv4.method auto || return
  else
    nmcli connection modify "$connection_id" \
      ipv4.never-default yes \
      ipv6.never-default yes || return
  fi

  log "Ethernet profile '$connection_id' updated to avoid default gateway usage."
}

main() {
  require_nmcli
  log "Applying wlan0/wlan1 role swap in NetworkManager profiles."

  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    connection_type="${line##*:}"
    connection_id="${line%:"$connection_type"}"
    connection_id="${connection_id//\\:/:}"
    connection_id="${connection_id//\\\\/\\}"

    case "$connection_type" in
      wifi)
        if ! configure_wifi_profile "$connection_id"; then
          log "WARNING: failed to configure wifi profile '$connection_id'; skipping."
        fi
        ;;
      ethernet)
        if ! configure_ethernet_profile "$connection_id"; then
          log "WARNING: failed to configure ethernet profile '$connection_id'; skipping."
        fi
        ;;
    esac
  done < <(LC_ALL=C nmcli --terse --fields NAME,TYPE connection show)

  log "Done. Reconnect interfaces or reboot for active sessions to pick up profile updates."
}

main "$@"
