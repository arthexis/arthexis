#!/usr/bin/env bash
set -e

usage() {
  echo "Usage: $0 [FILE [--set KEY VALUE|--unset KEY]]"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$BASE_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1
cd "$BASE_DIR"

list_envs() {
  for f in *.env; do
    [ -e "$f" ] && echo "$f"
  done
}

if [ $# -eq 0 ]; then
  list_envs
  exit 0
fi

FILE="$1"
shift

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [ $# -eq 0 ]; then
  nano "$FILE"
  exit 0
fi

case "$1" in
  --set)
    [ $# -eq 3 ] || { usage >&2; exit 1; }
    KEY="$2"
    VAL="$3"
    if grep -q "^${KEY}=" "$FILE"; then
      sed -i "s/^${KEY}=.*/${KEY}=${VAL}/" "$FILE"
    else
      echo "${KEY}=${VAL}" >> "$FILE"
    fi
    ;;
  --unset)
    [ $# -eq 2 ] || { usage >&2; exit 1; }
    KEY="$2"
    sed -i "/^${KEY}=.*/d" "$FILE"
    ;;
  -h|--help)
    usage
    exit 0
    ;;
  *)
    usage >&2
    exit 1
    ;;
esac
