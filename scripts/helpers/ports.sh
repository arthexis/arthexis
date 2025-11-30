#!/usr/bin/env bash

# Utility helpers for determining the configured backend port.

# arthexis_detect_backend_port BASE_DIR [FALLBACK]
#
# Detect the configured backend port for the installation rooted at BASE_DIR.
# If no persisted configuration exists, FALLBACK (default: 8888) is returned.
arthexis_detect_backend_port() {
    local base_dir="$1"
    local fallback="${2:-8888}"
    local lock_file="$base_dir/.locks/backend_port.lck"

    if [ -f "$lock_file" ]; then
        # shellcheck disable=SC2002
        local value
        value="$(cat "$lock_file" | tr -d '\r\n[:space:]')"
        if [[ "$value" =~ ^[0-9]+$ ]]; then
            if [ "$value" -ge 1 ] && [ "$value" -le 65535 ]; then
                printf '%s\n' "$value"
                return 0
            fi
        fi
    fi

    printf '%s\n' "$fallback"
}
