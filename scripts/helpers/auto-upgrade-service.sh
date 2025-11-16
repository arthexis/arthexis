#!/usr/bin/env bash

arthexis_repair_auto_upgrade_workdir() {
  local base_dir="$1"
  local service="$2"
  local systemd_dir_override="$3"

  if [ -z "$base_dir" ] || [ -z "$service" ]; then
    return 0
  fi

  local systemd_dir="${systemd_dir_override:-${SYSTEMD_DIR:-/etc/systemd/system}}"
  local unit_file="${systemd_dir}/${service}-auto-upgrade.service"
  local working_line="WorkingDirectory=${base_dir}"

  if [ ! -f "$unit_file" ]; then
    return 0
  fi

  local updated=0
  if grep -Eq '^WorkingDirectory=' "$unit_file"; then
    if ! grep -Fq "$working_line" "$unit_file"; then
      if [ ${#SUDO_CMD[@]} -gt 0 ]; then
        "${SUDO_CMD[@]}" sed -i "s|^WorkingDirectory=.*|${working_line}|" "$unit_file"
      else
        sed -i "s|^WorkingDirectory=.*|${working_line}|" "$unit_file"
      fi
      updated=1
    fi
  else
    if [ ${#SUDO_CMD[@]} -gt 0 ]; then
      "${SUDO_CMD[@]}" sed -i "/^\\[Service\\]/a ${working_line}" "$unit_file"
    else
      sed -i "/^\\[Service\\]/a ${working_line}" "$unit_file"
    fi
    updated=1
  fi

  if [ "$updated" -eq 1 ]; then
    if [ ${#SYSTEMCTL_CMD[@]} -gt 0 ]; then
      "${SYSTEMCTL_CMD[@]}" daemon-reload >/dev/null 2>&1 || true
      "${SYSTEMCTL_CMD[@]}" reset-failed "${service}-auto-upgrade.service" >/dev/null 2>&1 || true
    fi
    echo "Updated ${service}-auto-upgrade.service working directory to ${base_dir}"
  fi
}
