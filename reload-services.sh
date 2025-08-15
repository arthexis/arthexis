#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"

# Activate virtual environment if present
if [ -d .venv ]; then
  source .venv/bin/activate
fi

python manage.py systemctl_unit restart
