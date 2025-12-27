#!/usr/bin/env bash

# Lightweight timing utilities for install and upgrade workflows.
# Records timing information in the work/ directory for later analysis.

if [ -z "${BASE_DIR:-}" ]; then
  BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi

ARTHEXIS_TIMING_RUN_NAME=""
ARTHEXIS_TIMING_FILE=""
ARTHEXIS_TIMING_ID="${ARTHEXIS_TIMING_ID:-""}"
declare -Ag ARTHEXIS_TIMING_STARTS

arthexis_timing_now_ms() {
  local python_bin=""
  if command -v python3 >/dev/null 2>&1; then
    python_bin="python3"
  elif command -v python >/dev/null 2>&1; then
    python_bin="python"
  fi

  if [ -n "$python_bin" ]; then
    "$python_bin" - <<'PY'
import time
print(int(time.time() * 1000))
PY
    return
  fi

  if date +%s%3N >/dev/null 2>&1; then
    date +%s%3N
  else
    printf '%s000\n' "$(date +%s)"
  fi
}

arthexis_timing_setup() {
  local run_name="${1:-$(basename "$0" .sh)}"
  local stamp="${ARTHEXIS_TIMING_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
  local work_dir="$BASE_DIR/work"

  mkdir -p "$work_dir"
  ARTHEXIS_TIMING_RUN_NAME="$run_name"
  ARTHEXIS_TIMING_FILE="$work_dir/${run_name}-timings-${stamp}.log"
  printf '# %s run started at %s\n' "$run_name" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$ARTHEXIS_TIMING_FILE"
}

arthexis_timing_record() {
  local label="$1"
  local duration_ms="$2"
  local status="${3:-completed}"

  if [ -z "$ARTHEXIS_TIMING_FILE" ]; then
    return 0
  fi

  printf '%s\t%s\t%s\t%s\t%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "$ARTHEXIS_TIMING_RUN_NAME" \
    "$label" \
    "$duration_ms" \
    "$status" >>"$ARTHEXIS_TIMING_FILE"
}

arthexis_timing_start() {
  local label="$1"
  ARTHEXIS_TIMING_STARTS["$label"]="$(arthexis_timing_now_ms)"
}

arthexis_timing_end() {
  local label="$1"
  local status="${2:-completed}"
  local start_time="${ARTHEXIS_TIMING_STARTS[$label]:-}"

  if [ -z "$start_time" ]; then
    return 0
  fi

  local end_time
  end_time="$(arthexis_timing_now_ms)"
  local duration_ms=$((end_time - start_time))
  arthexis_timing_record "$label" "$duration_ms" "$status"
  unset "ARTHEXIS_TIMING_STARTS[$label]"
}
