#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"

INCLUDE_GREEN="${SUITE_UP_INCLUDE_GREEN:-0}"
ONLY_TARGET="${SUITE_UP_ONLY:-}"
RESTART_UNCHANGED="${SUITE_UP_RESTART_UNCHANGED:-0}"
DRY_RUN=0
SUITE_UP_TARGETS="${SUITE_UP_TARGETS:-arthexis,audi,porsche}"
SUITE_UP_GREEN_TARGETS="${SUITE_UP_GREEN_TARGETS:-audi,porsche}"

usage() {
  cat <<'USAGE'
Usage: ./scripts/suite-up.sh [options]

Options:
  --include-green       Include green targets in upgrade runs.
  --only NAME           Upgrade only the named target.
  --restart-unchanged   Force restart even if target revision is unchanged.
  --dry-run             Print planned actions without executing upgrade.sh.
  -h, --help            Show this help.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --include-green)
      INCLUDE_GREEN=1
      ;;
    --only)
      shift
      ONLY_TARGET="${1:-}"
      [[ -n "$ONLY_TARGET" ]] || { echo "--only requires NAME" >&2; exit 1; }
      ;;
    --restart-unchanged)
      RESTART_UNCHANGED=1
      ;;
    --dry-run)
      DRY_RUN=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
  shift
done

IFS=', ' read -r -a targets <<< "$SUITE_UP_TARGETS"
IFS=', ' read -r -a green_targets <<< "$SUITE_UP_GREEN_TARGETS"

is_green() {
  local value="$1"
  local target
  for target in "${green_targets[@]}"; do
    if [[ "$target" == "$value" ]]; then
      return 0
    fi
  done
  return 1
}

if [[ -n "$ONLY_TARGET" ]]; then
  found=0
  for target in "${targets[@]}"; do
    if [[ "$target" == "$ONLY_TARGET" ]]; then
      found=1
      break
    fi
  done
  if [[ "$found" -ne 1 ]]; then
    echo "Unknown target for --only: $ONLY_TARGET" >&2
    exit 1
  fi
fi

planned=()
skipped=()
protected=()
upgraded=()

for target in "${targets[@]}"; do
  if [[ -n "$ONLY_TARGET" && "$target" != "$ONLY_TARGET" ]]; then
    skipped+=("$target")
    continue
  fi

  if is_green "$target" && [[ "$INCLUDE_GREEN" -ne 1 ]]; then
    protected+=("$target")
    continue
  fi

  planned+=("$target")
done

if [[ ${#planned[@]} -eq 0 ]]; then
  echo "No targets selected for upgrade."
else
  for target in "${planned[@]}"; do
    cmd=("$BASE_DIR/upgrade.sh" "--latest")
    if [[ "$RESTART_UNCHANGED" -ne 1 ]]; then
      cmd+=("--pre-check")
    fi

    if [[ "$DRY_RUN" -eq 1 ]]; then
      echo "[$target] SERVICE_NAME=$target ${cmd[*]} (dry-run)"
      continue
    fi

    echo "[$target] SERVICE_NAME=$target ${cmd[*]}"
    SERVICE_NAME="$target" "${cmd[@]}"
    upgraded+=("$target")
  done
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "Summary: planned=${planned[*]:-none} skipped=${skipped[*]:-none} protected=${protected[*]:-none}"
else
  echo "Summary: upgraded=${upgraded[*]:-none} skipped=${skipped[*]:-none} protected=${protected[*]:-none}"
fi
