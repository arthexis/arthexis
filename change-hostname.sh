#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 NEW_HOSTNAME" >&2
  exit 1
fi

new_hostname="$1"

if [ "$(id -u)" -ne 0 ]; then
  echo "This script requires sudo. Please run as root." >&2
  exit 1
fi

current_hostname="$(hostname)"

hostnamectl set-hostname "$new_hostname"
echo "$new_hostname" > /etc/hostname
if [ -f /etc/hosts ]; then
  sed -i "s/\b${current_hostname}\b/${new_hostname}/g" /etc/hosts
fi

echo "Hostname changed from ${current_hostname} to ${new_hostname}. Please reboot for all changes to take effect."
