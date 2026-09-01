#!/usr/bin/env bash

# Helper functions for tracking systemd units via lockfiles.

_arthexis_systemd_lock_file() {
  local lock_dir="$1"

  printf "%s/systemd_services.lck" "$lock_dir"
}

arthexis_detect_service_user() {
  # Prefer the owner of the project directory so services run as the install user.
  local base_dir="$1"
  if [ -n "$base_dir" ] && [ -d "$base_dir" ]; then
    if stat -c '%U' "$base_dir" >/dev/null 2>&1; then
      stat -c '%U' "$base_dir"
      return 0
    fi
  fi
  id -un
}

arthexis_record_systemd_unit() {
  local lock_dir="$1"
  local unit_name="$2"

  if [ -z "$lock_dir" ] || [ -z "$unit_name" ]; then
    return 0
  fi

  local lock_file
  lock_file="$(_arthexis_systemd_lock_file "$lock_dir")"

  mkdir -p "$lock_dir"
  if [ -f "$lock_file" ]; then
    if grep -Fxq "$unit_name" "$lock_file"; then
      return 0
    fi
  fi

  echo "$unit_name" >> "$lock_file"
}

arthexis_install_lcd_service_unit() {
  return 0
}

arthexis_install_rfid_service_unit() {
  local base_dir="$1"
  local lock_dir="$2"
  local service_name="$3"

  if [ -z "$base_dir" ] || [ -z "$lock_dir" ] || [ -z "$service_name" ]; then
    return 0
  fi

  local systemd_dir="${SYSTEMD_DIR:-/etc/systemd/system}"
  local rfid_service
  rfid_service="rfid-${service_name}"
  local rfid_service_file
  rfid_service_file="${systemd_dir}/${rfid_service}.service"
  local rfid_service_user
  rfid_service_user="$(arthexis_detect_service_user "$base_dir")"
  local rfid_supplementary_groups=""
  if getent group gpio >/dev/null 2>&1; then
    rfid_supplementary_groups="${rfid_supplementary_groups:+$rfid_supplementary_groups }gpio"
  fi
  if getent group spi >/dev/null 2>&1; then
    rfid_supplementary_groups="${rfid_supplementary_groups:+$rfid_supplementary_groups }spi"
  fi
  local rfid_supplementary_groups_line=""
  if [ -n "$rfid_supplementary_groups" ]; then
    rfid_supplementary_groups_line="SupplementaryGroups=$rfid_supplementary_groups"
  fi

  sudo bash -c "cat > '$rfid_service_file'" <<SERVICEEOF
[Unit]
Description=RFID scanner service for Arthexis
After=${service_name}.service network-online.target
Wants=${service_name}.service
PartOf=${service_name}.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$base_dir
EnvironmentFile=-$base_dir/redis.env
EnvironmentFile=-$base_dir/debug.env
ExecStart=$base_dir/.venv/bin/python -m apps.cards.rfid_service
Restart=always
TimeoutStartSec=500
TimeoutStopSec=15
StandardOutput=journal
StandardError=journal
User=$rfid_service_user
$rfid_supplementary_groups_line

[Install]
WantedBy=multi-user.target
WantedBy=${service_name}.service
SERVICEEOF

  sudo systemctl daemon-reload
  sudo systemctl enable "$rfid_service"
  arthexis_record_systemd_unit "$lock_dir" "${rfid_service}.service"
}

arthexis_install_camera_service_unit() {
  local base_dir="$1"
  local lock_dir="$2"
  local service_name="$3"

  if [ -z "$base_dir" ] || [ -z "$lock_dir" ] || [ -z "$service_name" ]; then
    return 0
  fi

  local systemd_dir="${SYSTEMD_DIR:-/etc/systemd/system}"
  local camera_service
  camera_service="camera-${service_name}"
  local camera_service_file
  camera_service_file="${systemd_dir}/${camera_service}.service"
  local camera_service_user
  camera_service_user="$(arthexis_detect_service_user "$base_dir")"

  sudo tee "$camera_service_file" > /dev/null <<SERVICEEOF
[Unit]
Description=Camera capture service for Arthexis
After=${service_name}.service network-online.target redis.service
Wants=${service_name}.service
PartOf=${service_name}.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$base_dir
EnvironmentFile=-$base_dir/redis.env
EnvironmentFile=-$base_dir/debug.env
ExecStart=$base_dir/.venv/bin/python manage.py camera_service
Restart=always
TimeoutStartSec=500
StandardOutput=journal
StandardError=journal
User=$camera_service_user

[Install]
WantedBy=multi-user.target
WantedBy=${service_name}.service
SERVICEEOF

  sudo systemctl daemon-reload
  sudo systemctl enable "$camera_service"
  arthexis_record_systemd_unit "$lock_dir" "${camera_service}.service"
}

arthexis_read_env_file_value() {
  local base_dir="$1"
  local key="$2"

  if [ -z "$base_dir" ] || [ -z "$key" ] || [ ! -r "$base_dir/arthexis.env" ]; then
    return 0
  fi

  local python_bin=""
  if [ -x "$base_dir/.venv/bin/python" ]; then
    python_bin="$base_dir/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    python_bin="$(command -v python3)"
  else
    return 0
  fi

  "$python_bin" - "$base_dir/arthexis.env" "$key" <<'PYCODE'
import shlex
import sys
from pathlib import Path

env_file = Path(sys.argv[1])
key = sys.argv[2]
value = ""
try:
    lines = env_file.read_text(encoding="utf-8").splitlines()
except OSError:
    lines = []

for raw_line in lines:
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    candidate_key, candidate_value = line.split("=", 1)
    if candidate_key.strip() != key:
        continue
    candidate_value = candidate_value.strip()
    try:
        parts = shlex.split(candidate_value, comments=True, posix=True)
    except ValueError:
        parts = []
    value = parts[0] if parts else candidate_value.strip("\"'")

if value:
    print(value)
PYCODE
}

arthexis_configured_imager_burn_device() {
  local base_dir="$1"
  local device

  device="$(arthexis_read_env_file_value "$base_dir" IMAGER_GWAY_BURN_DEVICE)"
  if [ -n "$device" ]; then
    printf '%s\n' "$device"
    return 0
  fi
  arthexis_read_env_file_value "$base_dir" IMAGER_BURN_DEVICE
}

arthexis_udev_glob_escape() {
  printf '%s' "$1" | sed 's/[\\"]/\\&/g'
}

arthexis_install_control_usb_stability_rules() {
  local base_dir="$1"

  if [ -z "$base_dir" ]; then
    return 0
  fi
  if ! command -v sudo >/dev/null 2>&1; then
    return 0
  fi

  local udev_dir="${UDEV_RULES_DIR:-/etc/udev/rules.d}"
  local wifi_rule="${udev_dir}/92-arthexis-control-usb-wifi-power.rules"
  local burner_rule="${udev_dir}/93-arthexis-imager-burner-ignore.rules"
  local burn_device

  sudo install -d -m 0755 "$udev_dir"
  sudo tee "$wifi_rule" > /dev/null <<'RULEEOF'
# Keep common Realtek USB Wi-Fi uplinks awake during heavy SD-card burn I/O.
ACTION=="add|change", SUBSYSTEM=="usb", ATTR{idVendor}=="0bda", ATTR{idProduct}=="b812", TEST=="power/control", ATTR{power/control}="on"
ACTION=="add|change", SUBSYSTEM=="usb", ATTR{idVendor}=="0bda", ATTR{idProduct}=="b812", TEST=="power/autosuspend", ATTR{power/autosuspend}="-1"
RULEEOF

  burn_device="$(arthexis_configured_imager_burn_device "$base_dir")"
  case "$burn_device" in
    /dev/disk/by-id/*)
      local escaped_device
      escaped_device="$(arthexis_udev_glob_escape "$burn_device")"
      sudo tee "$burner_rule" > /dev/null <<RULEEOF
# Keep the configured Arthexis SD-card burner out of desktop automount paths.
ACTION=="add|change", SUBSYSTEM=="block", ENV{DEVLINKS}=="*${escaped_device}*", ENV{UDISKS_IGNORE}="1", ENV{UDISKS_AUTO}="0"
RULEEOF
      ;;
    *)
      sudo tee "$burner_rule" > /dev/null <<'RULEEOF'
# Arthexis SD-card burner automount suppression is not configured yet.
# Set IMAGER_GWAY_BURN_DEVICE or IMAGER_BURN_DEVICE to a stable /dev/disk/by-id/... path.
RULEEOF
      ;;
  esac

  if command -v udevadm >/dev/null 2>&1; then
    sudo udevadm control --reload-rules || true
    case "$burn_device" in
      /dev/disk/by-id/*)
        local resolved_burn_device
        resolved_burn_device="$(readlink -f "$burn_device" 2>/dev/null || true)"
        if [ -n "$resolved_burn_device" ]; then
          sudo udevadm trigger --action=change --subsystem-match=block --name-match="$resolved_burn_device" || true
        fi
        ;;
    esac
  fi
}

arthexis_install_systemd_timer_override() {
  local lock_dir="$1"
  local unit_name="$2"
  local on_boot_sec="$3"
  local on_unit_active_sec="$4"
  local randomized_delay_sec="$5"
  local note="$6"

  if [ -z "$lock_dir" ] || [ -z "$unit_name" ]; then
    return 0
  fi
  if ! command -v sudo >/dev/null 2>&1; then
    return 0
  fi

  local systemd_dir="${SYSTEMD_DIR:-/etc/systemd/system}"
  local dropin_dir="${systemd_dir}/${unit_name}.d"
  local override_file="${dropin_dir}/10-arthexis-control-usb-polling.conf"

  sudo install -d -m 0755 "$dropin_dir"
  sudo tee "$override_file" > /dev/null <<TIMEROVERRIDEEOF
[Timer]
# Installed by Arthexis to keep Control-node USB polling from amplifying
# hotplug churn on shared USB trees with Realtek uplinks and storage keys.
# $note
OnBootSec=
OnUnitActiveSec=
RandomizedDelaySec=
OnBootSec=$on_boot_sec
OnUnitActiveSec=$on_unit_active_sec
RandomizedDelaySec=$randomized_delay_sec
TIMEROVERRIDEEOF

  sudo chmod 0644 "$override_file"
}

arthexis_install_control_usb_polling_timer_overrides() {
  local lock_dir="$1"
  local preserve_existing="${2:-false}"

  if [ -z "$lock_dir" ]; then
    return 0
  fi

  local inventory_on_boot="${ARTHEXIS_USB_INVENTORY_TIMER_ON_BOOT_SEC:-2min}"
  local inventory_on_active="${ARTHEXIS_USB_INVENTORY_TIMER_ON_UNIT_ACTIVE_SEC:-5min}"
  local inventory_randomized="${ARTHEXIS_USB_INVENTORY_TIMER_RANDOMIZED_DELAY_SEC:-30s}"
  local bastion_on_boot="${ARTHEXIS_BASTION_USB_REFRESH_TIMER_ON_BOOT_SEC:-3min}"
  local bastion_on_active="${ARTHEXIS_BASTION_USB_REFRESH_TIMER_ON_UNIT_ACTIVE_SEC:-10min}"
  local bastion_randomized="${ARTHEXIS_BASTION_USB_REFRESH_TIMER_RANDOMIZED_DELAY_SEC:-60s}"
  local inventory_env_override=0
  local bastion_env_override=0
  local installed=0

  if [ -n "${ARTHEXIS_USB_INVENTORY_TIMER_ON_BOOT_SEC+x}" ] || \
    [ -n "${ARTHEXIS_USB_INVENTORY_TIMER_ON_UNIT_ACTIVE_SEC+x}" ] || \
    [ -n "${ARTHEXIS_USB_INVENTORY_TIMER_RANDOMIZED_DELAY_SEC+x}" ]; then
    inventory_env_override=1
  fi
  if [ -n "${ARTHEXIS_BASTION_USB_REFRESH_TIMER_ON_BOOT_SEC+x}" ] || \
    [ -n "${ARTHEXIS_BASTION_USB_REFRESH_TIMER_ON_UNIT_ACTIVE_SEC+x}" ] || \
    [ -n "${ARTHEXIS_BASTION_USB_REFRESH_TIMER_RANDOMIZED_DELAY_SEC+x}" ]; then
    bastion_env_override=1
  fi

  if [ "$preserve_existing" != true ] || \
    [ "$inventory_env_override" -eq 1 ] || \
    ! arthexis_systemd_timer_override_present "arthexis-usb-inventory.timer"; then
    arthexis_install_systemd_timer_override \
      "$lock_dir" \
      "arthexis-usb-inventory.timer" \
      "$inventory_on_boot" \
      "$inventory_on_active" \
      "$inventory_randomized" \
      "Override ARTHEXIS_USB_INVENTORY_TIMER_* to trade faster inventory refreshes for more USB bus activity."
    installed=1
  fi

  if [ "$preserve_existing" != true ] || \
    [ "$bastion_env_override" -eq 1 ] || \
    ! arthexis_systemd_timer_override_present "bastion-usb-refresh.timer"; then
    arthexis_install_systemd_timer_override \
      "$lock_dir" \
      "bastion-usb-refresh.timer" \
      "$bastion_on_boot" \
      "$bastion_on_active" \
      "$bastion_randomized" \
      "Override ARTHEXIS_BASTION_USB_REFRESH_TIMER_* when faster unlock-key polling is required."
    installed=1
  fi

  if [ "$installed" -eq 1 ] && command -v systemctl >/dev/null 2>&1 && command -v sudo >/dev/null 2>&1; then
    sudo systemctl daemon-reload || true
    sudo systemctl try-restart arthexis-usb-inventory.timer bastion-usb-refresh.timer || true
  fi
}

arthexis_remove_systemd_timer_override() {
  local unit_name="$1"

  if [ -z "$unit_name" ]; then
    return 0
  fi
  if ! command -v sudo >/dev/null 2>&1; then
    return 0
  fi

  local systemd_dir="${SYSTEMD_DIR:-/etc/systemd/system}"
  local dropin_dir="${systemd_dir}/${unit_name}.d"
  local override_file="${dropin_dir}/10-arthexis-control-usb-polling.conf"

  if [ ! -e "$override_file" ]; then
    return 0
  fi
  sudo rm -f "$override_file"
  sudo rmdir "$dropin_dir" 2>/dev/null || true
}

arthexis_systemd_timer_override_present() {
  local unit_name="$1"

  if [ -z "$unit_name" ]; then
    return 1
  fi

  local systemd_dir="${SYSTEMD_DIR:-/etc/systemd/system}"
  local dropin_dir="${systemd_dir}/${unit_name}.d"
  local override_file="${dropin_dir}/10-arthexis-control-usb-polling.conf"

  [ -e "$override_file" ]
}

arthexis_remove_control_usb_polling_timer_overrides() {
  local removed=0

  if arthexis_systemd_timer_override_present "arthexis-usb-inventory.timer"; then
    arthexis_remove_systemd_timer_override "arthexis-usb-inventory.timer"
    removed=1
  fi
  if arthexis_systemd_timer_override_present "bastion-usb-refresh.timer"; then
    arthexis_remove_systemd_timer_override "bastion-usb-refresh.timer"
    removed=1
  fi

  if [ "$removed" -eq 1 ] && command -v systemctl >/dev/null 2>&1 && command -v sudo >/dev/null 2>&1; then
    sudo systemctl daemon-reload || true
    sudo systemctl try-restart arthexis-usb-inventory.timer bastion-usb-refresh.timer || true
  fi
}

arthexis_install_imager_burner_service_unit() {
  local base_dir="$1"
  local lock_dir="$2"
  local service_name="${3:-arthexis}"

  if [ -z "$base_dir" ] || [ -z "$lock_dir" ]; then
    return 0
  fi

  local systemd_dir="${SYSTEMD_DIR:-/etc/systemd/system}"
  local unit_name="arthexis-imager-burner.service"
  local service_file="${systemd_dir}/${unit_name}"
  local service_user
  service_user="$(arthexis_detect_service_user "$base_dir")"
  local supplementary_groups_line=""
  if getent group disk >/dev/null 2>&1; then
    supplementary_groups_line="SupplementaryGroups=disk"
  fi

  sudo tee "$service_file" > /dev/null <<SERVICEEOF
[Unit]
Description=Durable SD-card burner worker for Arthexis
After=${service_name}.service network-online.target
Wants=${service_name}.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$base_dir
Environment="PYTHONPATH=$base_dir"
EnvironmentFile=-$base_dir/arthexis.env
EnvironmentFile=-$base_dir/redis.env
EnvironmentFile=-$base_dir/debug.env
ExecStart=$base_dir/.venv/bin/python manage.py imager burn work --loop --interval 15
Restart=always
TimeoutStartSec=500
TimeoutStopSec=15
StandardOutput=journal
StandardError=journal
User=$service_user
$supplementary_groups_line

[Install]
WantedBy=multi-user.target
WantedBy=${service_name}.service
SERVICEEOF

  arthexis_install_control_usb_stability_rules "$base_dir"
  sudo systemctl daemon-reload
  sudo systemctl enable "$unit_name"
  arthexis_record_systemd_unit "$lock_dir" "$unit_name"
}

arthexis_remove_systemd_unit_record() {
  local lock_dir="$1"
  local unit_name="$2"

  if [ -z "$lock_dir" ] || [ -z "$unit_name" ]; then
    return 0
  fi

  local lock_file
  lock_file="$(_arthexis_systemd_lock_file "$lock_dir")"

  if [ ! -f "$lock_file" ]; then
    return 0
  fi

  local tmp_file
  tmp_file="${lock_file}.tmp"
  grep -Fxv "$unit_name" "$lock_file" > "$tmp_file" || true
  mv "$tmp_file" "$lock_file"

  if [ ! -s "$lock_file" ]; then
    rm -f "$lock_file"
  fi
}

arthexis_read_systemd_unit_records() {
  local lock_dir="$1"

  if [ -z "$lock_dir" ]; then
    return 0
  fi

  local lock_file
  lock_file="$(_arthexis_systemd_lock_file "$lock_dir")"

  if [ ! -f "$lock_file" ]; then
    return 0
  fi

  cat "$lock_file"
}

arthexis_systemd_unit_recorded() {
  local lock_dir="$1"
  local unit_name="$2"

  if [ -z "$lock_dir" ] || [ -z "$unit_name" ]; then
    return 1
  fi

  local lock_file
  lock_file="$(_arthexis_systemd_lock_file "$lock_dir")"

  if [ ! -f "$lock_file" ]; then
    return 1
  fi

  grep -Fxq -- "$unit_name" "$lock_file"
}

arthexis_install_service_stack() {
  local base_dir="$1"
  local lock_dir="$2"
  local service_name="$3"
  local enable_celery="${4:-false}"
  local exec_cmd="$5"
  local service_mode="${6:-embedded}"
  local enable_boot_upgrade="${7:-false}"

  if [ -z "$base_dir" ] || [ -z "$lock_dir" ] || [ -z "$service_name" ]; then
    return 0
  fi

  if [ -z "$exec_cmd" ]; then
    exec_cmd="$base_dir/scripts/service-start.sh"
  fi

  if [ "$service_mode" != "systemd" ]; then
    return 0
  fi

  local manage_celery="$enable_celery"

  local systemd_dir="${SYSTEMD_DIR:-/etc/systemd/system}"
  local service_file="${systemd_dir}/${service_name}.service"
  local service_user
  service_user="$(arthexis_detect_service_user "$base_dir")"
  local prestart_requires=""
  local prestart_after=""

  if [ "$enable_boot_upgrade" = true ]; then
    arthexis_install_boot_upgrade_service_unit "$base_dir" "$lock_dir" "$service_name"
    prestart_requires="Requires=${service_name}-boot-upgrade.service"
    prestart_after="After=${service_name}-boot-upgrade.service"
  fi

  sudo bash -c "cat > '$service_file'" <<SERVICEEOF
[Unit]
Description=Arthexis Constellation Django service
After=network-online.target
Wants=network-online.target
${prestart_requires}
${prestart_after}

[Service]
Type=simple
WorkingDirectory=$base_dir
EnvironmentFile=-$base_dir/arthexis.env
EnvironmentFile=-$base_dir/redis.env
EnvironmentFile=-$base_dir/debug.env
ExecStart=$exec_cmd
Restart=always
TimeoutStartSec=500
User=$service_user

[Install]
WantedBy=multi-user.target
SERVICEEOF

  arthexis_record_systemd_unit "$lock_dir" "${service_name}.service"

  local celery_service=""
  local celery_service_file=""
  local celery_beat_service=""
  local celery_beat_service_file=""

  if [ "$manage_celery" = true ]; then
    celery_service="celery-${service_name}"
    celery_service_file="${systemd_dir}/${celery_service}.service"
    sudo bash -c "cat > '$celery_service_file'" <<CELERYSERVICEEOF
[Unit]
Description=Celery Worker for $service_name
After=${service_name}.service network-online.target redis.service
Requires=${service_name}.service
PartOf=${service_name}.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$base_dir
Environment="PYTHONPATH=$base_dir"
EnvironmentFile=-$base_dir/arthexis.env
EnvironmentFile=-$base_dir/redis.env
EnvironmentFile=-$base_dir/debug.env
ExecStart="$base_dir/.venv/bin/python" -m celery -A config worker -l info --concurrency=1 -n worker.${service_name}@%%h
Restart=always
TimeoutStartSec=500
User=$service_user

[Install]
WantedBy=multi-user.target
CELERYSERVICEEOF
    arthexis_record_systemd_unit "$lock_dir" "${celery_service}.service"

    celery_beat_service="celery-beat-${service_name}"
    celery_beat_service_file="${systemd_dir}/${celery_beat_service}.service"
    sudo bash -c "cat > '$celery_beat_service_file'" <<BEATSERVICEEOF
[Unit]
Description=Celery Beat for $service_name
After=${service_name}.service network-online.target redis.service
Requires=${service_name}.service
PartOf=${service_name}.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$base_dir
Environment="PYTHONPATH=$base_dir"
EnvironmentFile=-$base_dir/arthexis.env
EnvironmentFile=-$base_dir/redis.env
EnvironmentFile=-$base_dir/debug.env
ExecStart="$base_dir/.venv/bin/python" -m celery -A config beat -l info
Restart=always
TimeoutStartSec=500
User=$service_user

[Install]
WantedBy=multi-user.target
BEATSERVICEEOF
    arthexis_record_systemd_unit "$lock_dir" "${celery_beat_service}.service"
  fi

  sudo systemctl daemon-reload
  sudo systemctl enable "$service_name"
  if [ "$manage_celery" = true ]; then
    sudo systemctl enable "$celery_service" "$celery_beat_service"
  fi
}

arthexis_install_boot_upgrade_service_unit() {
  local base_dir="$1"
  local lock_dir="$2"
  local service_name="$3"

  if [ -z "$base_dir" ] || [ -z "$lock_dir" ] || [ -z "$service_name" ]; then
    return 0
  fi

  local systemd_dir="${SYSTEMD_DIR:-/etc/systemd/system}"
  local unit_name="${service_name}-boot-upgrade.service"
  local service_file="${systemd_dir}/${unit_name}"
  local service_user
  service_user="$(arthexis_detect_service_user "$base_dir")"

  sudo bash -c "cat > '$service_file'" <<SERVICEEOF
[Unit]
Description=Arthexis pre-start upgrade for ${service_name}
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$base_dir
ExecStart=$base_dir/scripts/boot-upgrade-prestart.sh --base-dir $base_dir --service $service_name
TimeoutStartSec=900
User=$service_user

[Install]
WantedBy=multi-user.target
SERVICEEOF

  sudo systemctl daemon-reload
  sudo systemctl enable "$unit_name"
  arthexis_record_systemd_unit "$lock_dir" "$unit_name"
}

arthexis_update_systemd_service_user() {
  # Ensure existing systemd units run as the owner of the project directory.
  local base_dir="$1"
  local lock_dir="$2"
  local systemd_dir="${SYSTEMD_DIR:-/etc/systemd/system}"

  if [ -z "$lock_dir" ]; then
    return 0
  fi

  local service_user
  service_user="$(arthexis_detect_service_user "$base_dir")"

  local updated=0
  local unit raw_unit
  while IFS= read -r unit; do
    raw_unit="$unit"
    unit="${unit%$'\r'}"
    [ -z "$unit" ] && continue
    case "$unit" in
      *-kiosk-layout-cleanup.service|*-kiosk-layout-cleanup.path)
        if declare -F arthexis_remove_systemd_unit_if_present >/dev/null; then
          arthexis_remove_systemd_unit_if_present "$lock_dir" "$unit"
        fi
        continue
        ;;
    esac
    local service_file="${systemd_dir}/${unit}"
    [ ! -f "$service_file" ] && continue
    if grep -Fq "User=${service_user}" "$service_file"; then
      continue
    fi
    if grep -Eq '^User=' "$service_file"; then
      sudo sed -i "s/^User=.*/User=${service_user}/" "$service_file" || continue
    else
      sudo sed -i "/^\[Service\]/a User=${service_user}" "$service_file" || continue
    fi
    updated=1
  done < <(arthexis_read_systemd_unit_records "$lock_dir")

  if [ "$updated" -eq 1 ]; then
    sudo systemctl daemon-reload
  fi
}
