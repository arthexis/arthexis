#!/usr/bin/env bash

ARTHEXIS_SERVICE_MODE_EMBEDDED="embedded"
ARTHEXIS_SERVICE_MODE_SYSTEMD="systemd"
ARTHEXIS_LCD_LOCK="lcd_screen.lck"

# shellcheck source=scripts/helpers/systemd_locks.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/systemd_locks.sh"

_arthexis_service_mode_lock_file() {
  local lock_dir="$1"
  printf "%s/service_mode.lck" "$lock_dir"
}

arthexis_detect_service_mode() {
  local lock_dir="$1"
  local default_mode="$ARTHEXIS_SERVICE_MODE_EMBEDDED"

  local systemd_dir
  systemd_dir="${SYSTEMD_DIR:-/etc/systemd/system}"

  if [ -z "$lock_dir" ]; then
    echo "$default_mode"
    return
  fi

  local lock_file
  lock_file="$(_arthexis_service_mode_lock_file "$lock_dir")"
  if [ -f "$lock_file" ]; then
    local mode
    mode=$(tr -d '\r\n' < "$lock_file" | tr 'A-Z' 'a-z')
    case "$mode" in
      "$ARTHEXIS_SERVICE_MODE_SYSTEMD"|"$ARTHEXIS_SERVICE_MODE_EMBEDDED")
        echo "$mode"
        return
        ;;
    esac
  fi

  local service_lock
  service_lock="$lock_dir/service.lck"
  if [ -f "$service_lock" ]; then
    local service_name
    service_name=$(tr -d '\r\n' < "$service_lock")
    if [ -n "$service_name" ]; then
      local candidate_units
      candidate_units=(
        "${service_name}.service"
        "celery-${service_name}.service"
        "celery-beat-${service_name}.service"
        "lcd-${service_name}.service"
      )

      local unit_name
      for unit_name in "${candidate_units[@]}"; do
        if command -v systemctl >/dev/null 2>&1; then
          if systemctl list-unit-files | awk '{print $1}' | grep -Fxq "$unit_name"; then
            echo "$ARTHEXIS_SERVICE_MODE_SYSTEMD"
            return
          fi
        fi

        if [ -f "${systemd_dir}/${unit_name}" ]; then
          echo "$ARTHEXIS_SERVICE_MODE_SYSTEMD"
          return
        fi
      done
    fi
  fi

  echo "$default_mode"
}

arthexis_record_service_mode() {
  local lock_dir="$1"
  local mode
  mode=$(printf '%s' "${2:-}" | tr 'A-Z' 'a-z')
  if [ -z "$lock_dir" ]; then
    return 0
  fi

  case "$mode" in
    "$ARTHEXIS_SERVICE_MODE_SYSTEMD"|"$ARTHEXIS_SERVICE_MODE_EMBEDDED")
      ;;
    *)
      mode="$ARTHEXIS_SERVICE_MODE_EMBEDDED"
      ;;
  esac

  local lock_file
  lock_file="$(_arthexis_service_mode_lock_file "$lock_dir")"
  mkdir -p "$lock_dir"
  printf '%s\n' "$mode" > "$lock_file"
}

arthexis_using_systemd_mode() {
  local lock_dir="$1"
  local mode
  mode=$(arthexis_detect_service_mode "$lock_dir")
  [ "$mode" = "$ARTHEXIS_SERVICE_MODE_SYSTEMD" ]
}

arthexis_using_embedded_mode() {
  local lock_dir="$1"
  local mode
  mode=$(arthexis_detect_service_mode "$lock_dir")
  [ "$mode" = "$ARTHEXIS_SERVICE_MODE_EMBEDDED" ]
}

arthexis_stop_embedded_lcd_processes() {
  local lock_dir="$1"

  pkill -f "python -m apps.screens\\.lcd_screen" 2>/dev/null || true
  pkill -f "apps/screens/lcd_screen.py" 2>/dev/null || true

  if [ -n "$lock_dir" ]; then
    rm -f "$lock_dir/lcd.pid"
  fi
}

arthexis_start_systemd_unit_if_present() {
  local unit_name="$1"

  if [ -z "$unit_name" ] || ! command -v systemctl >/dev/null 2>&1; then
    return 0
  fi

  if systemctl list-unit-files | awk '{print $1}' | grep -Fxq "$unit_name"; then
    if command -v sudo >/dev/null 2>&1; then
      sudo systemctl start "$unit_name" || true
    else
      systemctl start "$unit_name" || true
    fi
  fi
}

arthexis_disable_lcd_modes() {
  local lock_dir="$1"
  local service_name="$2"

  arthexis_stop_embedded_lcd_processes "$lock_dir"

  if [ -n "$service_name" ]; then
    arthexis_stop_systemd_unit_if_present "lcd-${service_name}.service"
  fi
}

arthexis_stop_systemd_unit_if_present() {
  local unit_name="$1"

  if [ -z "$unit_name" ] || ! command -v systemctl >/dev/null 2>&1; then
    return 0
  fi

  if systemctl list-unit-files | awk '{print $1}' | grep -Fxq "$unit_name"; then
    if command -v sudo >/dev/null 2>&1; then
      sudo systemctl stop "$unit_name" || true
    else
      systemctl stop "$unit_name" || true
    fi
  fi
}

arthexis_remove_systemd_unit_if_present() {
  local lock_dir="$1"
  local unit_name="$2"
  if [ -z "$unit_name" ]; then
    return 0
  fi

  local systemd_dir="${SYSTEMD_DIR:-/etc/systemd/system}"
  local unit_file="${systemd_dir}/${unit_name}"

  arthexis_stop_systemd_unit_if_present "$unit_name"

  if command -v systemctl >/dev/null 2>&1; then
    if systemctl list-unit-files | awk '{print $1}' | grep -Fxq "$unit_name"; then
      if command -v sudo >/dev/null 2>&1; then
        sudo systemctl disable "$unit_name" || true
      else
        systemctl disable "$unit_name" || true
      fi
    fi
  fi

  if [ -f "$unit_file" ]; then
    if command -v sudo >/dev/null 2>&1; then
      sudo rm "$unit_file"
    else
      rm "$unit_file"
    fi
    if command -v systemctl >/dev/null 2>&1; then
      if command -v sudo >/dev/null 2>&1; then
        sudo systemctl daemon-reload || true
      else
        systemctl daemon-reload || true
      fi
    fi
  fi

  if [ -n "$lock_dir" ]; then
    arthexis_remove_systemd_unit_record "$lock_dir" "$unit_name"
  fi
}

arthexis_remove_celery_unit_stack() {
  local lock_dir="$1"
  local service_name="$2"
  if [ -z "$service_name" ]; then
    return 0
  fi

  arthexis_remove_systemd_unit_if_present "$lock_dir" "celery-${service_name}.service"
  arthexis_remove_systemd_unit_if_present "$lock_dir" "celery-beat-${service_name}.service"
}

arthexis_service_unit_names() {
  local service_name="$1"
  local include_celery="${2:-false}"
  local include_lcd="${3:-false}"

  if [ -z "$service_name" ]; then
    return 0
  fi

  local -a units=("${service_name}.service")
  if [ "$include_celery" = true ]; then
    units+=("celery-${service_name}.service" "celery-beat-${service_name}.service")
  fi
  if [ "$include_lcd" = true ]; then
    units+=("lcd-${service_name}.service")
  fi

  printf "%s\n" "${units[@]}"
}

arthexis_stop_service_unit_stack() {
  local service_name="$1"
  local include_celery="${2:-false}"
  local include_lcd="${3:-false}"

  if [ -z "$service_name" ]; then
    return 0
  fi

  local unit
  while IFS= read -r unit; do
    arthexis_stop_systemd_unit_if_present "$unit"
  done < <(arthexis_service_unit_names "$service_name" "$include_celery" "$include_lcd")
}

arthexis_remove_service_unit_stack() {
  local lock_dir="$1"
  local service_name="$2"
  local include_celery="${3:-false}"
  local include_lcd="${4:-false}"

  if [ -z "$service_name" ]; then
    return 0
  fi

  local unit
  while IFS= read -r unit; do
    arthexis_remove_systemd_unit_if_present "$lock_dir" "$unit"
  done < <(arthexis_service_unit_names "$service_name" "$include_celery" "$include_lcd")
}

_arthexis_lcd_feature_lock_file() {
  local lock_dir="$1"

  printf "%s/%s" "$lock_dir" "$ARTHEXIS_LCD_LOCK"
}

arthexis_lcd_feature_enabled() {
  local lock_dir="$1"
  if [ -z "$lock_dir" ]; then
    return 1
  fi

  local lock_file
  lock_file="$(_arthexis_lcd_feature_lock_file "$lock_dir")"
  if [ ! -f "$lock_file" ]; then
    return 1
  fi

  local first_line
  first_line=$(head -n 1 "$lock_file" 2>/dev/null | tr -d '\r\n')
  if printf '%s' "$first_line" | grep -iq '^state='; then
    local state
    state=$(printf '%s' "${first_line#state=}" | tr 'A-Z' 'a-z')
    [ "$state" != "disabled" ]
    return $?
  fi

  return 0
}

arthexis_enable_lcd_feature_flag() {
  local lock_dir="$1"
  if [ -z "$lock_dir" ]; then
    return 0
  fi

  mkdir -p "$lock_dir"
  local lock_file
  lock_file="$(_arthexis_lcd_feature_lock_file "$lock_dir")"
  if [ -f "$lock_file" ]; then
    local first_line rest
    first_line=$(head -n 1 "$lock_file" 2>/dev/null)
    if printf '%s' "$first_line" | grep -iq '^state='; then
      rest=$(tail -n +2 "$lock_file" 2>/dev/null || true)
    else
      rest=$(cat "$lock_file" 2>/dev/null || true)
    fi
    if [ -n "$rest" ]; then
      printf "state=enabled\n%s\n" "$rest" > "$lock_file"
    else
      printf "state=enabled\n\n\n" > "$lock_file"
    fi
  else
    printf "state=enabled\n\n\n" > "$lock_file"
  fi
}

arthexis_disable_lcd_feature_flag() {
  local lock_dir="$1"
  if [ -z "$lock_dir" ]; then
    return 0
  fi

  local lock_file
  lock_file="$(_arthexis_lcd_feature_lock_file "$lock_dir")"
  if [ -f "$lock_file" ]; then
    local first_line rest
    first_line=$(head -n 1 "$lock_file" 2>/dev/null)
    if printf '%s' "$first_line" | grep -iq '^state='; then
      rest=$(tail -n +2 "$lock_file" 2>/dev/null || true)
    else
      rest=$(cat "$lock_file" 2>/dev/null || true)
    fi
    if [ -n "$rest" ]; then
      printf "state=disabled\n%s\n" "$rest" > "$lock_file"
    else
      printf "state=disabled\n\n\n" > "$lock_file"
    fi
  else
    mkdir -p "$lock_dir"
    printf "state=disabled\n\n\n" > "$lock_file"
  fi
}
