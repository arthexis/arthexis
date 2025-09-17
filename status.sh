#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/helpers/logging.sh
. "$BASE_DIR/scripts/helpers/logging.sh"
arthexis_resolve_log_dir "$BASE_DIR" LOG_DIR || exit 1
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1

LOCK_DIR="$BASE_DIR/locks"

# Determine installation status
if [ -d "$BASE_DIR/.venv" ]; then
  INSTALLED=true
else
  INSTALLED=false
fi

echo "Application installed: $INSTALLED"

SERVICE=""
if [ -f "$LOCK_DIR/service.lck" ]; then
  SERVICE="$(cat "$LOCK_DIR/service.lck")"
  echo "Service: $SERVICE"
else
  echo "Service: not installed"
fi

# Determine nginx mode and port
MODE="internal"
if [ -f "$LOCK_DIR/nginx_mode.lck" ]; then
  MODE="$(cat "$LOCK_DIR/nginx_mode.lck")"
fi
PORT=8888
if [ "$MODE" = "public" ]; then
  PORT=8000
fi

echo "Nginx mode: $MODE"

ROLE="${NODE_ROLE:-Terminal}"
if [ -z "$NODE_ROLE" ] && [ -f "$LOCK_DIR/role.lck" ]; then
  ROLE="$(cat "$LOCK_DIR/role.lck")"
fi
echo "Node role: $ROLE"

# Features
if [ -f "$LOCK_DIR/celery.lck" ]; then
  CELERY_FEATURE=true
else
  CELERY_FEATURE=false
fi
if [ -f "$LOCK_DIR/lcd_screen.lck" ]; then
  LCD_FEATURE=true
else
  LCD_FEATURE=false
fi
if [ -f "$LOCK_DIR/control.lck" ]; then
  CONTROL_FEATURE=true
else
  CONTROL_FEATURE=false
fi

echo "Features:"
echo "  Celery: $CELERY_FEATURE"
echo "  LCD screen: $LCD_FEATURE"
echo "  Control: $CONTROL_FEATURE"

echo "Checking running status..."
RUNNING=false
if [ -n "$SERVICE" ] && command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files | grep -Fq "${SERVICE}.service"; then
  STATUS=$(systemctl is-active "$SERVICE" || true)
  echo "  Service status: $STATUS"
  [ "$STATUS" = "active" ] && RUNNING=true
else
  if pgrep -f "manage.py runserver" >/dev/null 2>&1; then
    RUNNING=true
    # Try to detect port from running process
    PROC_PORT=$(pgrep -af "manage.py runserver" | sed -n 's/.*0\.0\.0\.0:\([0-9]*\).*/\1/p' | head -n1)
    if [ -n "$PROC_PORT" ]; then
      PORT="$PROC_PORT"
    fi
  fi
  echo "  Process running: $RUNNING"
fi

if [ "$RUNNING" = true ]; then
  echo "Application reachable at: http://localhost:$PORT"
else
  echo "Application is not running"
fi

# Celery status
if [ "$CELERY_FEATURE" = true ]; then
  if [ -n "$SERVICE" ] && command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files | grep -Fq "celery-$SERVICE.service"; then
    C_STATUS=$(systemctl is-active "celery-$SERVICE" || true)
    B_STATUS=$(systemctl is-active "celery-beat-$SERVICE" || true)
    echo "  Celery worker status: $C_STATUS"
    echo "  Celery beat status: $B_STATUS"
  else
    if pgrep -f "celery -A config" >/dev/null 2>&1; then
      echo "  Celery processes: running"
    else
      echo "  Celery processes: not running"
    fi
  fi
fi

if [ "$LCD_FEATURE" = true ]; then
  if [ -n "$SERVICE" ] && command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files | grep -Fq "lcd-$SERVICE.service"; then
    LCD_STATUS=$(systemctl is-active "lcd-$SERVICE" || true)
    echo "  LCD screen service status: $LCD_STATUS"
  fi
fi

# Node information
if command -v hostname >/dev/null 2>&1; then
  echo "Hostname: $(hostname)"
  echo "IP addresses: $(hostname -I 2>/dev/null || echo 'N/A')"
fi
