#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/helpers/logging.sh
. "$BASE_DIR/scripts/helpers/logging.sh"
arthexis_resolve_log_dir "$BASE_DIR" LOG_DIR || exit 1
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

if [ $# -eq 0 ]; then
  nano "$FILE"
  exit 0
fi

case "$1" in
  --set)
    [ $# -eq 3 ] || { echo "Usage: $0 FILE --set KEY VALUE" >&2; exit 1; }
    KEY="$2"
    VAL="$3"
    if grep -q "^${KEY}=" "$FILE"; then
      sed -i "s/^${KEY}=.*/${KEY}=${VAL}/" "$FILE"
    else
      echo "${KEY}=${VAL}" >> "$FILE"
    fi
    ;;
  --unset)
    [ $# -eq 2 ] || { echo "Usage: $0 FILE --unset KEY" >&2; exit 1; }
    KEY="$2"
    sed -i "/^${KEY}=.*/d" "$FILE"
    ;;
  *)
    echo "Usage: $0 [FILE] [--set KEY VALUE|--unset KEY]" >&2
    exit 1
    ;;
fi
