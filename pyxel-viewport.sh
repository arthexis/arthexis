#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$SCRIPT_DIR"

WORK_DIR="${WORK_DIR:-$SCRIPT_DIR/work/pyxel_viewport}"
PYXEL_RUNNER="${PYXEL_RUNNER:-pyxel}"

usage() {
  cat <<'USAGE'
Usage: ./pyxel-viewport.sh [--work-dir PATH] [--pyxel-runner CMD]

Creates or refreshes the Pyxel viewport project in the work directory and
launches it immediately. Environment variables WORK_DIR and PYXEL_RUNNER can
also override the defaults.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --work-dir)
      if [[ $# -lt 2 ]]; then
        echo "--work-dir requires a path" >&2
        usage
        exit 1
      fi
      WORK_DIR="$2"
      shift 2
      ;;
    --pyxel-runner)
      if [[ $# -lt 2 ]]; then
        echo "--pyxel-runner requires a command" >&2
        usage
        exit 1
      fi
      PYXEL_RUNNER="$2"
      shift 2
      ;;
    --help|-h)
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

if [[ -e "$WORK_DIR" && ! -d "$WORK_DIR" ]]; then
  echo "WORK_DIR must be a directory: $WORK_DIR" >&2
  exit 1
fi

if [[ -d "$WORK_DIR" ]]; then
  if find "$WORK_DIR" -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
    echo "Refusing to clear non-empty work directory: $WORK_DIR" >&2
    echo "Please provide an empty directory or remove its contents manually." >&2
    exit 1
  fi
else
  mkdir -p "$WORK_DIR"
fi

python manage.py pyxel_viewport --output-dir "$WORK_DIR" --pyxel-runner "$PYXEL_RUNNER"
