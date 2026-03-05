#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"

export PYTHONPATH="$BASE_DIR${PYTHONPATH:+:$PYTHONPATH}"
exec "$BASE_DIR/scripts/resolve_cli.sh" "$@"
