#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: sudo scripts/instrument-nginx.sh [--user <username>]

Configure nginx permissions and sudoers entries so the Arthexis suite can
manage nginx configurations without interactive sudo prompts or permission
errors.

Options:
  --user <username>  Override the service user that should be granted access.
USAGE
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  echo "This script must be run as root (use sudo)." >&2
  exit 1
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
base_dir=$(cd -- "${script_dir}/.." && pwd)

service_user=""
if [ "${1:-}" = "--user" ]; then
  service_user="${2:-}"
  if [ -z "$service_user" ]; then
    echo "--user requires a username." >&2
    exit 1
  fi
elif stat -c '%U' "$base_dir" >/dev/null 2>&1; then
  service_user="$(stat -c '%U' "$base_dir")"
fi

if [ -z "$service_user" ] || [ "$service_user" = "root" ]; then
  service_user="${SUDO_USER:-}"
fi

if [ -z "$service_user" ]; then
  echo "Could not determine a non-root service user. Please specify one with --user <username>." >&2
  exit 1
fi

if ! id "$service_user" >/dev/null 2>&1; then
  echo "Service user '$service_user' not found." >&2
  exit 1
fi

sudoers_file="/etc/sudoers.d/arthexis-nginx"
sudoers_tmp="$(mktemp)"
cat <<SUDOERS_EOF > "$sudoers_tmp"
# Allow Arthexis ($service_user) to manage nginx without sudo prompts.
Defaults:$service_user !requiretty
Cmnd_Alias ARTHEXIS_NGINX = \
    /bin/systemctl reload nginx, /bin/systemctl restart nginx, \
    /bin/systemctl start nginx, /bin/systemctl stop nginx, \
    /bin/systemctl status nginx, \
    /usr/bin/systemctl reload nginx, /usr/bin/systemctl restart nginx, \
    /usr/bin/systemctl start nginx, /usr/bin/systemctl stop nginx, \
    /usr/bin/systemctl status nginx, \
    /usr/sbin/nginx -t, \
    /usr/local/sbin/nginx -t, \
    /sbin/nginx -t
$service_user ALL=(root) NOPASSWD: ARTHEXIS_NGINX
SUDOERS_EOF

if command -v visudo >/dev/null 2>&1; then
  if ! visudo -cf "$sudoers_tmp"; then
    rm -f "$sudoers_tmp"
    echo "Sudoers syntax validation failed." >&2
    exit 1
  fi
else
  rm -f "$sudoers_tmp"
  echo "visudo is required to validate sudoers entries." >&2
  exit 1
fi

mv "$sudoers_tmp" "$sudoers_file"
chmod 0440 "$sudoers_file"

nginx_paths=(
  /etc/nginx
  /etc/nginx/sites-available
  /etc/nginx/sites-enabled
  /etc/nginx/conf.d
  /etc/letsencrypt/options-ssl-nginx.conf
  /etc/letsencrypt/ssl-dhparams.pem
)

# For Let's Encrypt live certs, prefer group-based access rather than broad ACLs.
if getent group ssl-cert >/dev/null 2>&1; then
  usermod -aG ssl-cert "$service_user"
fi

if command -v setfacl >/dev/null 2>&1; then
  for path in "${nginx_paths[@]}"; do
    if [ -e "$path" ]; then
      setfacl -m "u:${service_user}:rwX" "$path"
      if [ -d "$path" ]; then
        setfacl -R -m "u:${service_user}:rwX" "$path"
        setfacl -d -m "u:${service_user}:rwX" "$path"
      fi
    fi
  done
else
  echo "Warning: setfacl not available; falling back to group permissions for access." >&2
  for path in "${nginx_paths[@]}"; do
    if [ -e "$path" ]; then
      chgrp -R "$service_user" "$path"
      chmod -R g+rwX "$path"
    fi
  done
fi

echo "Nginx instrumentation completed for user: $service_user"
