#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/launch/ubuntu.sh
. "$SCRIPT_DIR/ubuntu.sh"

ubuntu_launch_main "$(basename "$0")" "22.04" "$@"
