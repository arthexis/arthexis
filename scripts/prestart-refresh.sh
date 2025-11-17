#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# shellcheck source=scripts/helpers/logging.sh
. "$BASE_DIR/scripts/helpers/logging.sh"

cd "$BASE_DIR"

arthexis_resolve_log_dir "$BASE_DIR" LOG_DIR || exit 1
LOG_FILE="$LOG_DIR/startup.log"
exec >>"$LOG_FILE" 2>&1

printf '%s %s\n' "[$(date -Is)]" "Running prestart environment refresh"

# Skip failover branch creation during prestart refreshes and capture errors.
FAILOVER_CREATED=1 ./env-refresh.sh "$@"
