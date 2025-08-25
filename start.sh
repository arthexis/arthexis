#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"

# If a systemd service was installed, restart it instead of launching a new process
if [ -f SERVICE ]; then
  SERVICE_NAME="$(cat SERVICE)"
  if systemctl list-unit-files | grep -Fq "${SERVICE_NAME}.service"; then
    sudo systemctl restart "$SERVICE_NAME"
    # Show status information so the user can verify the service state
    sudo systemctl status "$SERVICE_NAME" --no-pager
    exit 0
  fi
fi

# Ensure virtual environment is available
if [ ! -d .venv ]; then
  echo "Virtual environment not found. Run ./install.sh first." >&2
  exit 1
fi
source .venv/bin/activate

# Default to port 8000 and disabled auto-reload unless --reload is provided
PORT=8000
RELOAD=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)
      PORT="$2"
      shift 2
      ;;
    --reload)
      RELOAD=true
      shift
      ;;
    *)
      echo "Usage: $0 [--port PORT] [--reload]" >&2
      exit 1
      ;;
  esac
done

# Start the Django development server
if [ "$RELOAD" = true ]; then
  python manage.py runserver 0.0.0.0:$PORT
else
  python manage.py runserver 0.0.0.0:$PORT --noreload
fi
