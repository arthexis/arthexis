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

    local user_home="/home/$username"
    if [ ! -d "$user_home" ]; then
        return
    fi

    local desktop_dir=""
    if command -v xdg-user-dir >/dev/null 2>&1; then
        if [ "$(id -un 2>/dev/null)" = "$username" ]; then
            desktop_dir="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
        else
            if command -v sudo >/dev/null 2>&1; then
                desktop_dir="$(sudo -H -u "$username" xdg-user-dir DESKTOP 2>/dev/null || true)"
            elif command -v runuser >/dev/null 2>&1; then
                desktop_dir="$(runuser -u "$username" -- xdg-user-dir DESKTOP 2>/dev/null || true)"
            fi
        fi
    fi

    if [ -z "$desktop_dir" ]; then
        desktop_dir="$user_home/Desktop"
    fi

    if [ ! -d "$desktop_dir" ]; then
        mkdir -p "$desktop_dir" || return
        if [ "$(id -un 2>/dev/null)" != "$username" ]; then
            local user_group
            if ! user_group="$(id -gn "$username" 2>/dev/null)"; then
                user_group="$username"
            fi
            chown "$username":"$user_group" "$desktop_dir" 2>/dev/null || true
        fi
    fi

    local public_shortcut="$desktop_dir/Arthexis Public Site.desktop"
    local admin_shortcut="$desktop_dir/Arthexis Admin Console.desktop"

    local script_base_dir
    script_base_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
    local launcher_path="$script_base_dir/scripts/helpers/desktop_shortcuts.sh"
    local public_exec
    local admin_exec
    printf -v public_exec '%q %q %q' "$launcher_path" launch public
    printf -v admin_exec '%q %q %q' "$launcher_path" launch admin

    arthexis_write_shortcut() {
        local target="$1"

        local tmpfile
        tmpfile="$(mktemp "${desktop_dir}/.arthexis_shortcut.XXXXXX" 2>/dev/null)" || return 1

        if ! cat > "$tmpfile"; then
            rm -f "$tmpfile"
            return 1
        fi

        if [ -L "$target" ]; then
            rm -f "$target" || true
        elif [ -e "$target" ] && [ ! -f "$target" ]; then
            rm -f "$tmpfile"
            return 1
        fi

        chmod 755 "$tmpfile" 2>/dev/null || true

        if mv -f "$tmpfile" "$target"; then
            return 0
        fi

        rm -f "$tmpfile"
        return 1
    }

    arthexis_write_shortcut "$public_shortcut" <<SHORTCUT
[Desktop Entry]
Version=1.0
Type=Application
Name=Arthexis Public Site
Comment=Open the Arthexis public site
Exec=$public_exec
Icon=web-browser
Terminal=false
Categories=Network;WebBrowser;
StartupNotify=true
SHORTCUT

    arthexis_write_shortcut "$admin_shortcut" <<SHORTCUT
[Desktop Entry]
Version=1.0
Type=Application
Name=Arthexis Admin Console
Comment=Open the Arthexis admin console
Exec=$admin_exec
Icon=applications-system
Terminal=false
Categories=Office;System;
StartupNotify=true
SHORTCUT

    if [ "$(id -un 2>/dev/null)" != "$username" ]; then
        local user_group
        if ! user_group="$(id -gn "$username" 2>/dev/null)"; then
            user_group="$username"
        fi
        chown "$username":"$user_group" "$public_shortcut" "$admin_shortcut" 2>/dev/null || true
    fi
}

arthexis_desktop_shortcut_start_unit() {
    local unit="$1"
    if [ -z "$unit" ]; then
        return 1
    fi
    local start_cmd=(systemctl start "$unit")
    if ! command -v systemctl >/dev/null 2>&1; then
        return 1
    fi
    if command -v sudo >/dev/null 2>&1; then
        start_cmd=(sudo "${start_cmd[@]}")
    fi
    "${start_cmd[@]}" >/dev/null 2>&1 || return 1
    return 0
}

arthexis_desktop_shortcut_detect_port() {
    local base_dir="$1"
    local default_port="$(arthexis_detect_backend_port "$base_dir")"

    local service_lock="$base_dir/locks/service.lck"
    local service_name=""
    if [ -f "$service_lock" ]; then
        service_name="$(tr -d '\r\n' < "$service_lock" 2>/dev/null)"
    fi

    if [ -n "$service_name" ]; then
        local unit_file="/etc/systemd/system/${service_name}.service"
        if [ -f "$unit_file" ]; then
            local exec_line
            exec_line="$(grep -E '^ExecStart=' "$unit_file" 2>/dev/null | head -n1)"
            if [ -n "$exec_line" ]; then
                exec_line="${exec_line#ExecStart=}"
                local port
                port="$(printf '%s\n' "$exec_line" | sed -n 's/.*0\\.0\\.0\\.0:\([0-9]\{2,5\}\).*/\1/p')"
                if [ -z "$port" ]; then
                    port="$(printf '%s\n' "$exec_line" | sed -n 's/.*--port[= ]\([0-9]\{2,5\}\).*/\1/p')"
                fi
                if [ -n "$port" ]; then
                    printf '%s' "$port"
                    return 0
                fi
            fi
        fi
    fi

    if command -v pgrep >/dev/null 2>&1; then
        local runserver_line
        runserver_line="$(pgrep -af "manage.py runserver" 2>/dev/null | head -n1)"
        if [ -n "$runserver_line" ]; then
            local port
            port="$(printf '%s\n' "$runserver_line" | sed -n 's/.*0\\.0\\.0\\.0:\([0-9]\{2,5\}\).*/\1/p')"
            if [ -z "$port" ]; then
                port="$(printf '%s\n' "$runserver_line" | sed -n 's/.*--port[= ]\([0-9]\{2,5\}\).*/\1/p')"
            fi
            if [ -n "$port" ]; then
                printf '%s' "$port"
                return 0
            fi
        fi
    fi

    printf '%s' "$default_port"
    return 0
}

arthexis_desktop_shortcut_launch() {
    local shortcut="$1"
    local base_dir
    base_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
    local locks_dir="$base_dir/locks"
    local service_name=""
    local url=""
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

    local port
    port="$(arthexis_desktop_shortcut_detect_port "$base_dir")"
    if [ -z "$port" ]; then
        port="8000"
    fi
    url="http://localhost:${port}${port_suffix}"

    if [ -f "$locks_dir/service.lck" ]; then
        service_name="$(cat "$locks_dir/service.lck" 2>/dev/null)"
    fi

    local started=false
    if [ -n "$service_name" ] && command -v systemctl >/dev/null 2>&1; then
        if ! systemctl is-active --quiet "$service_name"; then
            if arthexis_desktop_shortcut_start_unit "$service_name"; then
                started=true
            fi
            if [ -f "$locks_dir/celery.lck" ]; then
                arthexis_desktop_shortcut_start_unit "celery-$service_name" || true
                arthexis_desktop_shortcut_start_unit "celery-beat-$service_name" || true
            fi
            if [ -f "$locks_dir/lcd_screen.lck" ] || [ -f "$locks_dir/lcd_screen_enabled.lck" ]; then
                arthexis_desktop_shortcut_start_unit "lcd-$service_name" || true
            fi
        fi
        if [ "$started" = true ]; then
            local attempt
            for attempt in 1 2 3 4 5; do
                if systemctl is-active --quiet "$service_name"; then
                    break
                fi
                sleep 1
            done
            sleep 1
        fi
    fi

    local browser=""
    if command -v firefox >/dev/null 2>&1; then
        browser="firefox"
    elif command -v xdg-open >/dev/null 2>&1; then
        browser="xdg-open"
    elif command -v sensible-browser >/dev/null 2>&1; then
        browser="sensible-browser"
    fi

    if [ -z "$browser" ]; then
        echo "No suitable browser found to open $url" >&2
        return 1
    fi

    "${browser}" "$url" &
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    if [ "$1" = "launch" ]; then
        shift
        arthexis_desktop_shortcut_launch "$1"
    fi
fi
