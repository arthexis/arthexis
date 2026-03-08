#!/usr/bin/env bash

_ARTHEXIS_HELPER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/helpers/ports.sh
. "$_ARTHEXIS_HELPER_DIR/ports.sh"
unset _ARTHEXIS_HELPER_DIR

arthexis_refresh_desktop_shortcuts() {
    local base_dir="$1"
    if [ -z "$base_dir" ]; then
        return
    fi

    case "$base_dir" in
        /home/*)
            ;;
        *)
            return
            ;;
    esac

    local remainder="${base_dir#/home/}"
    if [ -z "$remainder" ]; then
        return
    fi

    local username="${remainder%%/*}"
    if [ -z "$username" ]; then
        return
    fi

    if ! id "$username" >/dev/null 2>&1; then
        return
    fi

    local python_exec="$base_dir/.venv/bin/python"
    if [ ! -x "$python_exec" ]; then
        python_exec="python3"
    fi

    "$python_exec" "$base_dir/manage.py" sync_desktop_shortcuts --base-dir "$base_dir" --username "$username" || true
}


arthexis_desktop_shortcut_start_unit() {
    local unit="$1"
    if [ -z "$unit" ]; then
        return 1
    fi
    if ! command -v systemctl >/dev/null 2>&1; then
        return 1
    fi

    local start_cmd=(systemctl start "$unit")
    if command -v sudo >/dev/null 2>&1; then
        start_cmd=(sudo "${start_cmd[@]}")
    fi
    "${start_cmd[@]}" >/dev/null 2>&1 || return 1
    return 0
}


arthexis_desktop_shortcut_open_url_fallback() {
    local url="$1"
    local browser=""
    if command -v xdg-open >/dev/null 2>&1; then
        browser="xdg-open"
    elif command -v sensible-browser >/dev/null 2>&1; then
        browser="sensible-browser"
    elif command -v firefox >/dev/null 2>&1; then
        browser="firefox"
    fi

    if [ -z "$browser" ]; then
        echo "No suitable browser found to open $url" >&2
        return 1
    fi

    "$browser" "$url" &
}


arthexis_desktop_shortcut_launch() {
    local shortcut="$1"
    local base_dir
    base_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

    local port_suffix="/"
    case "$shortcut" in
        public)
            port_suffix="/"
            ;;
        admin)
            port_suffix="/admin/"
            ;;
        *)
            echo "Unknown shortcut: $shortcut" >&2
            return 1
            ;;
    esac

    local python_exec="$base_dir/.venv/bin/python"
    if [ ! -x "$python_exec" ]; then
        python_exec="python3"
    fi

    local capability_json=""
    capability_json="$("$python_exec" "$base_dir/manage.py" desktop_launch_capabilities --base-dir "$base_dir" 2>/dev/null || true)"

    if [ -z "$capability_json" ]; then
        local fallback_port
        fallback_port="$(arthexis_detect_backend_port "$base_dir")"
        if [ -z "$fallback_port" ]; then
            fallback_port="8000"
        fi
        arthexis_desktop_shortcut_open_url_fallback "http://localhost:${fallback_port}${port_suffix}"
        return $?
    fi

    local resolved
    resolved="$(CAPABILITY_JSON="$capability_json" python3 - <<'PY'
import json
import os

raw = os.environ.get("CAPABILITY_JSON", "")

try:
    data = json.loads(raw)
except json.JSONDecodeError:
    print("invalid")
    raise SystemExit(0)

port = int(data.get("backend_port") or 8000)
service_name = str(data.get("service_name") or "").strip()
opener = str(data.get("browser_opener_command") or "").strip()
metadata = bool(data.get("metadata_available"))
systemd = bool(data.get("systemd_control_available"))
print(f"{port}\n{service_name}\n{opener}\n{1 if metadata else 0}\n{1 if systemd else 0}")
PY
)"

    if [ -z "$resolved" ] || [ "$resolved" = "invalid" ]; then
        local fallback_port
        fallback_port="$(arthexis_detect_backend_port "$base_dir")"
        if [ -z "$fallback_port" ]; then
            fallback_port="8000"
        fi
        arthexis_desktop_shortcut_open_url_fallback "http://localhost:${fallback_port}${port_suffix}"
        return $?
    fi

    local port service_name opener metadata_available systemd_available
    port="$(printf '%s\n' "$resolved" | sed -n '1p')"
    service_name="$(printf '%s\n' "$resolved" | sed -n '2p')"
    opener="$(printf '%s\n' "$resolved" | sed -n '3p')"
    metadata_available="$(printf '%s\n' "$resolved" | sed -n '4p')"
    systemd_available="$(printf '%s\n' "$resolved" | sed -n '5p')"

    if [ -z "$port" ]; then
        port="8000"
    fi

    local url="http://localhost:${port}${port_suffix}"

    if [ "$metadata_available" != "1" ]; then
        arthexis_desktop_shortcut_open_url_fallback "$url"
        return $?
    fi

    if [ "$systemd_available" = "1" ] && [ -n "$service_name" ] && command -v systemctl >/dev/null 2>&1; then
        if ! systemctl is-active --quiet "$service_name"; then
            arthexis_desktop_shortcut_start_unit "$service_name" || true
        fi
    fi

    if [ -n "$opener" ] && command -v "$opener" >/dev/null 2>&1; then
        "$opener" "$url" &
        return 0
    fi

    arthexis_desktop_shortcut_open_url_fallback "$url"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    if [ "$1" = "launch" ]; then
        shift
        arthexis_desktop_shortcut_launch "$1"
    fi
fi
