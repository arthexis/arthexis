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
cat <<SUDOERS_EOF > "$sudoers_file"
# Allow Arthexis ($service_user) to manage nginx without sudo prompts.
Defaults:$service_user !requiretty
Cmnd_Alias ARTHEXIS_NGINX = \
    /bin/mkdir, \
    /bin/ln, \
    /bin/rm, \
    /bin/cp, \
    /bin/systemctl reload nginx, /bin/systemctl start nginx, \
    /usr/bin/systemctl reload nginx, /usr/bin/systemctl start nginx, \
    /usr/sbin/nginx -t, \
    /usr/local/sbin/nginx -t, \
    /sbin/nginx -t
SUDOERS_EOF
chmod 0440 "$sudoers_file"

if command -v visudo >/dev/null 2>&1; then
  visudo -cf "$sudoers_file"
fi

nginx_paths=(
  /etc/nginx
  /etc/nginx/sites-available
  /etc/nginx/sites-enabled
  /etc/nginx/conf.d
  /etc/letsencrypt
  /etc/letsencrypt/live
  /etc/letsencrypt/options-ssl-nginx.conf
  /etc/letsencrypt/ssl-dhparams.pem
)

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
  echo "Warning: setfacl not available; falling back to chmod for read access." >&2
  for path in "${nginx_paths[@]}"; do
    if [ -e "$path" ]; then
      chmod -R o+rX "$path"
    fi
  done
fi

echo "Nginx instrumentation completed for user: $service_user"
