#!/usr/bin/env bash
# shellcheck shell=bash

arthexis_ensure_nginx_in_path() {
  if command -v nginx >/dev/null 2>&1; then
    return 0
  fi

  local -a extra_paths=("/usr/sbin" "/usr/local/sbin" "/sbin")
  local dir
  for dir in "${extra_paths[@]}"; do
    if [ -x "$dir/nginx" ]; then
      case ":$PATH:" in
        *":$dir:"*) ;;
        *) PATH="${PATH:+$PATH:}$dir"
           export PATH ;;
      esac
      if command -v nginx >/dev/null 2>&1; then
        return 0
      fi
    fi
  done

  return 1
}

ARTHEXIS_NGINX_DISABLED_LOCK="nginx_disabled.lck"

arthexis_nginx_disabled() {
  local base_dir="$1"
  if [ -z "$base_dir" ]; then
    return 1
  fi

  [ -f "$base_dir/.locks/$ARTHEXIS_NGINX_DISABLED_LOCK" ]
}

arthexis_disable_nginx() {
  local base_dir="$1"
  if [ -z "$base_dir" ]; then
    return 0
  fi

  mkdir -p "$base_dir/.locks"
  : > "$base_dir/.locks/$ARTHEXIS_NGINX_DISABLED_LOCK"
}

arthexis_enable_nginx() {
  local base_dir="$1"
  if [ -z "$base_dir" ]; then
    return 0
  fi

  rm -f "$base_dir/.locks/$ARTHEXIS_NGINX_DISABLED_LOCK"
}

arthexis_can_manage_nginx() {
  if ! command -v sudo >/dev/null 2>&1; then
    return 1
  fi

  if arthexis_ensure_nginx_in_path; then
    return 0
  fi

  if [ -d /etc/nginx ]; then
    return 0
  fi

  return 1
}

arthexis_reload_or_start_nginx() {
  if sudo systemctl reload nginx; then
    return 0
  fi

  echo "Warning: nginx reload failed; attempting to start nginx in case it is stopped."
  if sudo systemctl start nginx; then
    echo "nginx started successfully after reload failure."
    return 0
  fi

  echo "Manual intervention required: unable to start nginx. Ask an administrator to run 'sudo systemctl status nginx' and review nginx logs." >&2
  return 1
}

ARTHEXIS_SSL_OPTIONS_PATH="/etc/letsencrypt/options-ssl-nginx.conf"
ARTHEXIS_SSL_OPTIONS_URL="https://raw.githubusercontent.com/certbot/certbot/master/certbot-nginx/certbot_nginx/_internal/tls_configs/options-ssl-nginx.conf"

arthexis_detect_https_enabled() {
  local base_dir="$1"
  local desired_mode="${2:-}"
  local lock_dir="$base_dir/.locks"

  case "${desired_mode,,}" in
    https|public)
      return 0
      ;;
  esac

  if [ -f "$lock_dir/nginx_protocol.lck" ]; then
    local stored_protocol
    stored_protocol=$(tr -d '\r\n\t ' < "$lock_dir/nginx_protocol.lck" 2>/dev/null || true)
    if [ "${stored_protocol,,}" = "https" ]; then
      return 0
    fi
  fi

  local python_bin="$base_dir/.venv/bin/python"
  if [ -x "$python_bin" ] && [ -f "$base_dir/manage.py" ]; then
    if PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$base_dir" \
      DJANGO_SETTINGS_MODULE="config.settings" \
      "$python_bin" <<'PY'
from apps.nginx.models import SiteConfiguration
import sys

try:
    protocol = SiteConfiguration.get_default().protocol.lower()
except Exception:
    sys.exit(1)

sys.exit(0 if protocol == "https" else 1)
PY
    then
      return 0
    fi
  fi

  if [ -f "$lock_dir/nginx_mode.lck" ]; then
    local stored_mode
    stored_mode=$(tr -d '\r\n\t ' < "$lock_dir/nginx_mode.lck" 2>/dev/null || true)
    if [ "${stored_mode,,}" = "public" ]; then
      return 0
    fi
  fi

  return 1
}

arthexis_provision_ssl_options_file() {
  local base_dir="$1"
  local https_required="${2:-0}"
  local bundled_path="$base_dir/apps/nginx/options-ssl-nginx.conf"

  case "${https_required,,}" in
    1|true|yes|on)
      ;;
    *)
      return 0
      ;;
  esac

  if [ -f "$ARTHEXIS_SSL_OPTIONS_PATH" ]; then
    return 0
  fi

  if ! command -v sudo >/dev/null 2>&1; then
    echo "sudo is required to install SSL options at $ARTHEXIS_SSL_OPTIONS_PATH; skipping." >&2
    return 0
  fi

  local dest_dir
  dest_dir="$(dirname "$ARTHEXIS_SSL_OPTIONS_PATH")"
  if ! sudo mkdir -p "$dest_dir"; then
    echo "Warning: unable to create $dest_dir for SSL options file." >&2
    return 0
  fi

  if [ -f "$bundled_path" ]; then
    if sudo cp "$bundled_path" "$ARTHEXIS_SSL_OPTIONS_PATH"; then
      echo "Installed default nginx SSL options at $ARTHEXIS_SSL_OPTIONS_PATH."
      return 0
    fi
  fi

  if command -v curl >/dev/null 2>&1; then
    if sudo curl -fsSL "$ARTHEXIS_SSL_OPTIONS_URL" -o "$ARTHEXIS_SSL_OPTIONS_PATH"; then
      echo "Downloaded nginx SSL options from Certbot to $ARTHEXIS_SSL_OPTIONS_PATH."
      return 0
    fi
  fi

  echo "Warning: unable to provision default SSL options file at $ARTHEXIS_SSL_OPTIONS_PATH." >&2
}

arthexis_refresh_nginx_maintenance() {
  local base_dir="$1"
  shift || true
  local -a configs=("$@")
  local fallback_src="$base_dir/config/data/nginx/maintenance"
  local fallback_dest="/usr/share/arthexis-fallback"
  local update_script="$base_dir/scripts/helpers/update_nginx_maintenance.py"

  if [ ${#configs[@]} -eq 0 ]; then
    configs=("/etc/nginx/sites-enabled/arthexis.conf" \
      "/etc/nginx/conf.d/arthexis-internal.conf" \
      "/etc/nginx/conf.d/arthexis-public.conf")
  fi

  if [ -d "$fallback_src" ]; then
    if ! command -v sudo >/dev/null 2>&1; then
      echo "sudo is required to manage nginx maintenance assets" >&2
      return 1
    fi

    if ! sudo mkdir -p "$fallback_dest"; then
      echo "Failed to create nginx maintenance directory at $fallback_dest" >&2
      return 1
    fi

    if ! sudo cp -r "$fallback_src"/. "$fallback_dest"/; then
      echo "Failed to copy maintenance assets to $fallback_dest" >&2
      return 1
    fi
  else
    echo "Maintenance assets not found at $fallback_src" >&2
  fi

  if [ ! -f "$update_script" ]; then
    return 0
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 not available; skipping nginx maintenance configuration" >&2
    return 0
  fi

  local changed=0
  local status=0
  local conf
  for conf in "${configs[@]}"; do
    if [ ! -f "$conf" ]; then
      continue
    fi
    sudo python3 "$update_script" "$conf"
    status=$?
    if [ $status -eq 2 ]; then
      changed=1
    elif [ $status -ne 0 ]; then
      echo "Failed to update maintenance fallback for $conf" >&2
    fi
  done

  local nginx_available=0
  if arthexis_ensure_nginx_in_path && command -v nginx >/dev/null 2>&1; then
    nginx_available=1
  fi

  if [ $changed -eq 1 ] && [ $nginx_available -eq 1 ]; then
    if sudo nginx -t; then
      if ! arthexis_reload_or_start_nginx; then
        echo "Warning: nginx could not be reloaded or started automatically. Ask an administrator to review the service status." >&2
      fi
    else
      echo "Warning: nginx configuration test failed after maintenance update" >&2
    fi
  fi

  return 0
}
