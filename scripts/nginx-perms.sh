#!/usr/bin/env bash
set -euo pipefail

# Grant a user group-read access to Arthexis-managed nginx site config files.
# Run as root, for example: sudo ./scripts/nginx-perms.sh [username]

TARGET_USER="${1:-${SUDO_USER:-$USER}}"
TARGET_GROUP="$(id -gn "$TARGET_USER")"

for nginx_dir in /etc/nginx/sites-enabled /etc/nginx/sites-available; do
  if [[ ! -d "$nginx_dir" ]]; then
    continue
  fi

  while IFS= read -r -d '' conf; do
    chgrp "$TARGET_GROUP" "$conf"
    chmod g+r "$conf"
  done < <(find "$nginx_dir" -maxdepth 1 -type f -name 'arthexis*.conf' -print0)
done

echo "Granted group read access for arthexis nginx configs to $TARGET_USER ($TARGET_GROUP)."
