#!/usr/bin/env bash
# Usage: ./arthexis.sh <command> [args...]
# Native Unix entrypoint for the shared Arthexis lifecycle dispatcher.
set -e

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if command -v python3 >/dev/null 2>&1; then
    exec python3 "$SCRIPT_DIR/arthexis.py" "$@"
fi
if command -v python >/dev/null 2>&1; then
    exec python "$SCRIPT_DIR/arthexis.py" "$@"
fi

echo "arthexis: Python 3 is required." >&2
exit 127
