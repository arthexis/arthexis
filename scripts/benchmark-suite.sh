#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TIMING_HELPER="$BASE_DIR/scripts/helpers/timing.sh"

if [ ! -f "$TIMING_HELPER" ]; then
  echo "Timing helper not found at $TIMING_HELPER" >&2
  exit 1
fi

# shellcheck source=scripts/helpers/timing.sh
. "$TIMING_HELPER"

RUN_ID="${ARTHEXIS_TIMING_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
export ARTHEXIS_TIMING_ID="$RUN_ID"

WORK_DIR="$BASE_DIR/work"
SUMMARY_FILE="$WORK_DIR/suite-benchmark-${RUN_ID}.log"

SKIP_INSTALL=false
SKIP_START=false
SKIP_UPGRADE=false
INSTALL_ARGS=(--no-start --clean)
START_ARGS=()
UPGRADE_ARGS=()

usage() {
  cat <<'USAGE'
Usage: scripts/benchmark-suite.sh [options]

Options:
  --run-id ID          Override the benchmark run identifier (default: timestamp)
  --skip-install       Skip running install.sh
  --skip-start         Skip running start.sh
  --skip-upgrade       Skip running upgrade.sh
  --install-args "..."  Additional arguments to pass to install.sh (default: "--no-start --clean")
  --start-args "..."    Arguments to pass to start.sh
  --upgrade-args "..."  Arguments to pass to upgrade.sh
  -h, --help           Show this help text
USAGE
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --run-id)
        if [ -z "${2:-}" ]; then
          echo "--run-id requires a value" >&2
          exit 1
        fi
        RUN_ID="$2"
        export ARTHEXIS_TIMING_ID="$RUN_ID"
        shift 2
        ;;
      --skip-install)
        SKIP_INSTALL=true
        shift
        ;;
      --skip-start)
        SKIP_START=true
        shift
        ;;
      --skip-upgrade)
        SKIP_UPGRADE=true
        shift
        ;;
      --install-args)
        if [ -z "${2:-}" ]; then
          echo "--install-args requires a value" >&2
          exit 1
        fi
        # shellcheck disable=SC2206
        INSTALL_ARGS=(${2})
        shift 2
        ;;
      --start-args)
        if [ -z "${2:-}" ]; then
          echo "--start-args requires a value" >&2
          exit 1
        fi
        # shellcheck disable=SC2206
        START_ARGS=(${2})
        shift 2
        ;;
      --upgrade-args)
        if [ -z "${2:-}" ]; then
          echo "--upgrade-args requires a value" >&2
          exit 1
        fi
        # shellcheck disable=SC2206
        UPGRADE_ARGS=(${2})
        shift 2
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        echo "Unknown option: $1" >&2
        usage
        exit 1
        ;;
    esac
  done
}

record_timing() {
  local label="$1"
  local duration_ms="$2"
  local status="$3"
  printf '%s\t%s\t%s\t%s\t%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "$RUN_ID" \
    "$label" \
    "$duration_ms" \
    "$status" | tee -a "$SUMMARY_FILE"
}

human_seconds() {
  local duration_ms="$1"
  python3 - <<PY
import sys
ms = int(sys.argv[1])
print(f"{ms/1000:.2f}")
PY
}

run_step() {
  local label="$1"
  shift
  local start_ms end_ms duration_ms status
  start_ms="$(arthexis_timing_now_ms)"
  if "$@"; then
    status="completed"
  else
    status="failed"
  fi
  end_ms="$(arthexis_timing_now_ms)"
  duration_ms=$((end_ms - start_ms))
  record_timing "$label" "$duration_ms" "$status"
  local seconds
  seconds=$(human_seconds "$duration_ms")
  printf '%-10s %s seconds (%s)\n' "$label:" "$seconds" "$status"
  if [ "$status" != "completed" ]; then
    exit 1
  fi
}

record_skip() {
  local label="$1"
  record_timing "$label" 0 "skipped"
  printf '%-10s skipped\n' "$label:"
}

main() {
  parse_args "$@"
  mkdir -p "$WORK_DIR"
  : >"$SUMMARY_FILE"
  printf '# suite benchmark run %s\n' "$RUN_ID" >>"$SUMMARY_FILE"
  printf '# stages: install, start, upgrade\n' >>"$SUMMARY_FILE"
  printf 'timestamp\trun_id\tstage\tduration_ms\tstatus\n' >>"$SUMMARY_FILE"

  cd "$BASE_DIR"

  if [ "$SKIP_INSTALL" = true ]; then
    record_skip "install"
  else
    echo "Running install.sh ${INSTALL_ARGS[*]}" >&2
    run_step "install" "$BASE_DIR/install.sh" "${INSTALL_ARGS[@]}"
  fi

  if [ "$SKIP_START" = true ]; then
    record_skip "start"
  else
    echo "Running start.sh ${START_ARGS[*]}" >&2
    run_step "start" "$BASE_DIR/start.sh" "${START_ARGS[@]}"
  fi

  if [ "$SKIP_UPGRADE" = true ]; then
    record_skip "upgrade"
  else
    echo "Running upgrade.sh ${UPGRADE_ARGS[*]}" >&2
    run_step "upgrade" "$BASE_DIR/upgrade.sh" "${UPGRADE_ARGS[@]}"
  fi

  echo
  echo "Benchmark results written to $SUMMARY_FILE"
}

main "$@"
