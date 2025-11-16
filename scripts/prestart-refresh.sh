#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BASE_DIR"

# Skip failover branch creation during prestart refreshes.
FAILOVER_CREATED=1 ./env-refresh.sh
