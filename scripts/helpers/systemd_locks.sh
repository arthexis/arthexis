#!/usr/bin/env bash

# Helper functions for tracking systemd units via lockfiles.

_arthexis_systemd_lock_file() {
  local lock_dir="$1"

  printf "%s/systemd_services.lck" "$lock_dir"
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

# Install or update the main systemd service and optional Celery units.
arthexis_install_service_stack() {
  local base_dir="$1"
  local lock_dir="$2"
  local service_name="$3"
  local enable_celery="${4:-false}"
  local exec_cmd="$5"
  local service_mode="${6:-embedded}"
  local install_watchdog="${7:-false}"

  if [ -z "$base_dir" ] || [ -z "$lock_dir" ] || [ -z "$service_name" ]; then
    return 0
  fi

  if [ -z "$exec_cmd" ]; then
    exec_cmd="$base_dir/service-start.sh"
  fi

  local manage_celery="$enable_celery"
  if [ "${service_mode}" != "systemd" ]; then
    manage_celery=false
  fi

  local systemd_dir="${SYSTEMD_DIR:-/etc/systemd/system}"
  local service_file="${systemd_dir}/${service_name}.service"
  local service_user
  service_user="$(id -un)"

  sudo bash -c "cat > '$service_file'" <<SERVICEEOF
[Unit]
Description=Arthexis Constellation Django service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$base_dir
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
EnvironmentFile=-$base_dir/redis.env
EnvironmentFile=-$base_dir/debug.env
ExecStart=$base_dir/.venv/bin/celery -A config worker -l info --concurrency=1
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
EnvironmentFile=-$base_dir/redis.env
EnvironmentFile=-$base_dir/debug.env
ExecStart=$base_dir/.venv/bin/celery -A config beat -l info
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

  if [ "$install_watchdog" = true ]; then
    local watchdog_service
    watchdog_service="${service_name}-watchdog"
    local watchdog_service_file
    watchdog_service_file="${systemd_dir}/${watchdog_service}.service"
    local watchdog_exec
    watchdog_exec="$base_dir/scripts/helpers/watchdog.sh ${service_name}"

    sudo bash -c "cat > '$watchdog_service_file'" <<WATCHDOGEOF
[Unit]
Description=Arthexis suite watchdog for ${service_name}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$watchdog_exec
Restart=always
RestartSec=15
User=$service_user

[Install]
WantedBy=multi-user.target
WATCHDOGEOF

    arthexis_record_systemd_unit "$lock_dir" "${watchdog_service}.service"
    sudo systemctl daemon-reload
    sudo systemctl enable "$watchdog_service"
    sudo systemctl start "$watchdog_service"
  fi
}
