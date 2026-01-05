#!/usr/bin/env bash

SIDECARS_LOCK_NAME="sidecars.lck"

arthexis_sidecars_lock_file() {
  local base_dir="$1"
  if [ -z "$base_dir" ]; then
    return 1
  fi
  printf "%s/.locks/%s" "$base_dir" "$SIDECARS_LOCK_NAME"
}

arthexis_record_sidecar() {
  local base_dir="$1"
  local type="$2"
  local name="$3"
  local path="$4"
  local service="$5"

  if [ -z "$base_dir" ] || [ -z "$type" ] || [ -z "$name" ] || [ -z "$path" ]; then
    return 1
  fi

  local lock_file
  lock_file="$(arthexis_sidecars_lock_file "$base_dir")" || return 1
  mkdir -p "$(dirname "$lock_file")"

  local record
  record="${type}\t${name}\t${path}\t${service}"

  if [ -f "$lock_file" ] && grep -Fq "${type}\t${name}\t${path}" "$lock_file"; then
    return 0
  fi

  printf '%s\n' "$record" >> "$lock_file"
}

arthexis_remove_sidecar_record() {
  local base_dir="$1"
  local type="$2"
  local name="$3"

  if [ -z "$base_dir" ] || [ -z "$type" ] || [ -z "$name" ]; then
    return 1
  fi

  local lock_file
  lock_file="$(arthexis_sidecars_lock_file "$base_dir")" || return 1
  if [ ! -f "$lock_file" ]; then
    return 0
  fi

  local tmp_file
  tmp_file="${lock_file}.tmp"
  awk -v t="$type" -v n="$name" 'BEGIN{FS="\t"}{if(!($1==t && $2==n))print}' "$lock_file" > "$tmp_file" && mv "$tmp_file" "$lock_file"
}

arthexis_read_sidecar_records() {
  local base_dir="$1"
  local lock_file
  lock_file="$(arthexis_sidecars_lock_file "$base_dir")" || return 1
  if [ ! -f "$lock_file" ]; then
    return 0
  fi
  cat "$lock_file"
}
