#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"

# Activate virtual environment if present
if [ -d .venv ]; then
  source .venv/bin/activate
fi

# Default to port 8888 but allow override via first argument
PORT=${1:-8888}

# Start the Django development server
python manage.py runserver 0.0.0.0:$PORT
