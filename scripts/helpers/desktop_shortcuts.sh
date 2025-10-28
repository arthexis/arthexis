#!/usr/bin/env bash

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

    arthexis_write_shortcut "$public_shortcut" <<'SHORTCUT'
[Desktop Entry]
Version=1.0
Type=Application
Name=Arthexis Public Site
Comment=Open the Arthexis public site
Exec=xdg-open http://localhost/
Icon=web-browser
Terminal=false
Categories=Network;WebBrowser;
StartupNotify=true
SHORTCUT

    arthexis_write_shortcut "$admin_shortcut" <<'SHORTCUT'
[Desktop Entry]
Version=1.0
Type=Application
Name=Arthexis Admin Console
Comment=Open the Arthexis admin console
Exec=xdg-open http://localhost/admin/
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
