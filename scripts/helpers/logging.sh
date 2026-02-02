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

arthexis__path_has_symlink() {
    local path="$1"
    local current=""
    local IFS="/"

    [ -n "$path" ] || return 1

    case "$path" in
        /*)
            current="/"
            path="${path#/}"
            ;;
    esac

    read -r -a parts <<< "$path"
    for part in "${parts[@]}"; do
        [ -n "$part" ] || continue
        if [ "$current" = "/" ]; then
            current="/$part"
        elif [ -n "$current" ]; then
            current="$current/$part"
        else
            current="$part"
        fi
        if [ -L "$current" ]; then
            return 0
        fi
    done

    return 1
}

arthexis__secure_mkdir() {
    local candidate="$1"

    [ -n "$candidate" ] || return 1

    if arthexis__path_has_symlink "$candidate"; then
        return 1
    fi

    local old_umask
    old_umask=$(umask)
    umask 077
    if ! mkdir -p -m 700 "$candidate" >/dev/null 2>&1; then
        umask "$old_umask"
        return 1
    fi
    umask "$old_umask"

    if arthexis__path_has_symlink "$candidate"; then
        return 1
    fi

    if [ ! -d "$candidate" ] || [ ! -w "$candidate" ]; then
        return 1
    fi

    if [ -n "${EUID:-}" ] && [ "${EUID:-0}" -eq 0 ]; then
        local owner
        owner=$(stat -c '%u' "$candidate" 2>/dev/null || echo "")
        if [ "$owner" != "0" ]; then
            return 1
        fi
    fi

    return 0
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
        if arthexis__secure_mkdir "$candidate"; then
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

arthexis_secure_log_file() {
    local log_dir="$1"
    local script_name="$2"
    local __resultvar="$3"

    if [ -z "$log_dir" ] || [ -z "$script_name" ] || [ -z "$__resultvar" ]; then
        echo "arthexis_secure_log_file requires log directory, script name, and output variable" >&2
        return 1
    fi

    if ! arthexis__secure_mkdir "$log_dir"; then
        echo "Log directory $log_dir is not secure or writable." >&2
        return 1
    fi

    local prefix
    prefix="$(basename "$script_name" .sh)"
    local log_file=""
    log_file=$(mktemp -p "$log_dir" "${prefix}.log.XXXXXX" 2>/dev/null || true)
    if [ -z "$log_file" ]; then
        echo "Unable to create log file in $log_dir." >&2
        return 1
    fi

    printf -v "$__resultvar" '%s' "$log_file"
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

arthexis_clear_log_files() {
    local base_dir="$1"
    local log_dir="$2"
    local log_file="$3"
    local -a log_dirs=()

    if [ -n "$log_dir" ]; then
        log_dirs+=("$log_dir")
    fi

    if [ -n "$base_dir" ]; then
        local default_logs="$base_dir/logs"
        if [ -z "$log_dir" ] || [ "$log_dir" != "$default_logs" ]; then
            log_dirs+=("$default_logs")
        fi
    fi

    local dir
    for dir in "${log_dirs[@]}"; do
        if [ -d "$dir" ]; then
            echo "Clearing log files in $dir..."
            local -a find_args=("$dir" -type f ! -name ".gitkeep")
            if [ -n "$log_file" ]; then
                find_args+=(! -path "$log_file")
            fi
            find_args+=(-print -delete)
            find "${find_args[@]}"
        fi
    done
}
