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
