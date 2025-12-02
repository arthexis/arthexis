#!/usr/bin/env bash

arthexis__expand_path() {
    local path="$1"
    if [ -z "$path" ]; then
        echo ""
        return
    fi
    case "$path" in
        "~")
            printf '%s\n' "$HOME"
            ;;
        "~/"*)
            printf '%s/%s\n' "$HOME" "${path#~/}"
            ;;
        *)
            printf '%s\n' "$path"
            ;;
    esac
}

arthexis_resolve_log_dir() {
    local script_dir="$1"
    local __resultvar="$2"

    if [ -z "$script_dir" ] || [ -z "$__resultvar" ]; then
        echo "arthexis_resolve_log_dir requires script directory and output variable" >&2
        return 1
    fi

    local default="$script_dir/logs"
    local euid
    euid=${EUID:-$(id -u)}
    local -a candidates=()
    local -a attempted=()

    if [ -n "${ARTHEXIS_LOG_DIR:-}" ]; then
        candidates+=("$ARTHEXIS_LOG_DIR")
    fi

    if [ "$euid" -eq 0 ]; then
        # When running with elevated privileges, avoid writing logs inside the
        # repository tree so subsequent non-root processes (like CI cleanups)
        # do not encounter permission issues.
        candidates+=("/var/log/arthexis" "/tmp/arthexis/logs")
    else
        local state_home
        state_home="${XDG_STATE_HOME:-$HOME/.local/state}"
        candidates+=("$default" "$state_home/arthexis/logs" "$HOME/.arthexis/logs" "/tmp/arthexis/logs")
    fi

    local candidate=""
    local chosen=""
    for candidate in "${candidates[@]}"; do
        [ -n "$candidate" ] || continue
        candidate="$(arthexis__expand_path "$candidate")"
        attempted+=("$candidate")
        if mkdir -p "$candidate" >/dev/null 2>&1 && [ -d "$candidate" ] && [ -w "$candidate" ]; then
            chosen="$candidate"
            break
        fi
    done

    if [ -z "$chosen" ]; then
        local joined=""
        if [ "${#attempted[@]}" -gt 0 ]; then
            joined=$(printf '%s, ' "${attempted[@]}")
            joined=${joined%, }
        fi
        echo "Unable to create a writable log directory. Tried: ${joined:-none}" >&2
        return 1
    fi

    if [ "$chosen" != "$default" ]; then
        if [ "${#attempted[@]}" -gt 0 ] && [ "${attempted[0]}" = "$default" ]; then
            echo "Log directory $default is not writable; using $chosen" >&2
        elif [ "$euid" -eq 0 ] && [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER}" != "root" ] && [ -z "${ARTHEXIS_LOG_DIR:-}" ]; then
            echo "Running with elevated privileges; writing logs to $chosen" >&2
        fi
    fi

    export ARTHEXIS_LOG_DIR="$chosen"
    printf -v "$__resultvar" '%s' "$chosen"
    return 0
}

arthexis_log_startup_event() {
    local base_dir="$1"
    local script_name="$2"
    local event="$3"
    local detail="$4"

    if [ -z "$base_dir" ] || [ -z "$script_name" ] || [ -z "$event" ]; then
        return 0
    fi

    local log_file="$base_dir/logs/startup-report.log"
    mkdir -p "$(dirname "$log_file")" >/dev/null 2>&1 || true

    local timestamp
    timestamp=$(date -Iseconds)
    printf '%s\t%s\t%s\t%s\n' \
        "$timestamp" "$script_name" "$event" "${detail:-}" \
        >>"$log_file" 2>/dev/null || true
}
