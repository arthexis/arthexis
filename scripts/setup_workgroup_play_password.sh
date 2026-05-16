#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$BASE_DIR/arthexis.env"
SERVICE_FILE="/etc/systemd/system/arthexis-workgroup-play-password.service"
TIMER_FILE="/etc/systemd/system/arthexis-workgroup-play-password.timer"
TIMEZONE_VALUE="${ARTHEXIS_WORKGROUP_PASSWORD_TIMEZONE:-America/Monterrey}"
APPLY_NOW=false

usage() {
  cat >&2 <<'EOF'
Usage: sudo scripts/setup_workgroup_play_password.sh [--apply-now]

Install the systemd timer that rotates the local Unix play account to the
daily Workgroup password published by the suite at /workgroup/.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply-now)
      APPLY_NOW=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root so the script can install systemd units and update play." >&2
  exit 1
fi

if [ ! -d "$BASE_DIR/.venv" ]; then
  echo "Virtual environment not found at $BASE_DIR/.venv." >&2
  exit 1
fi

if ! id play >/dev/null 2>&1; then
  echo "Unix user 'play' does not exist." >&2
  exit 1
fi

set_env_value() {
  local key="$1"
  local value="$2"
  "$BASE_DIR/.venv/bin/python" "$BASE_DIR/manage.py" env --set "$key" "$value" >/dev/null
}

if [ ! -f "$ENV_FILE" ] || ! grep -q '^ARTHEXIS_WORKGROUP_PASSWORD_SEED=' "$ENV_FILE"; then
  seed="$("$BASE_DIR/.venv/bin/python" - <<'PY'
import secrets

print(secrets.token_urlsafe(48))
PY
)"
  set_env_value "ARTHEXIS_WORKGROUP_PASSWORD_SEED" "$seed"
fi

if [ ! -f "$ENV_FILE" ] || ! grep -q '^ARTHEXIS_WORKGROUP_PASSWORD_TIMEZONE=' "$ENV_FILE"; then
  set_env_value "ARTHEXIS_WORKGROUP_PASSWORD_TIMEZONE" "$TIMEZONE_VALUE"
fi

repo_owner="$(stat -c '%U:%G' "$BASE_DIR")"
chown "$repo_owner" "$ENV_FILE"
chmod 600 "$ENV_FILE"

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Rotate Arthexis Workgroup play SSH password
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$BASE_DIR
ExecStart=/bin/bash -lc 'set -a; . "$ENV_FILE"; set +a; . "$BASE_DIR/.venv/bin/activate"; exec python "$BASE_DIR/manage.py" workgroup_password --apply-user play'
EOF

cat > "$TIMER_FILE" <<'EOF'
[Unit]
Description=Daily Arthexis Workgroup play SSH password rotation

[Timer]
OnCalendar=*-*-* 00:01:00
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now arthexis-workgroup-play-password.timer

if [ "$APPLY_NOW" = true ]; then
  systemctl start arthexis-workgroup-play-password.service
fi

echo "Installed arthexis-workgroup-play-password.timer."
