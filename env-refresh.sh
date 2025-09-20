#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/helpers/logging.sh
. "$SCRIPT_DIR/scripts/helpers/logging.sh"
if [ -z "$SCRIPT_DIR" ] || [ "$SCRIPT_DIR" = "/" ]; then
  echo "Refusing to run from root directory." >&2
  exit 1
fi
cd "$SCRIPT_DIR"
arthexis_resolve_log_dir "$SCRIPT_DIR" LOG_DIR || exit 1
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1

BACKUP_DIR="$SCRIPT_DIR/backups"

backup_database_for_branch() {
  local branch="$1"
  local source="$SCRIPT_DIR/db.sqlite3"
  local backup_path="$BACKUP_DIR/${branch}.sqlite3"

  if [ ! -f "$source" ]; then
    return
  fi

  if ! mkdir -p "$BACKUP_DIR"; then
    echo "Failed to create backup directory at $BACKUP_DIR" >&2
    return
  fi

  if cp -p "$source" "$backup_path"; then
    echo "Saved database backup to backups/${branch}.sqlite3"
  else
    echo "Failed to create database backup at $backup_path" >&2
  fi
}

create_failover_branch() {
  local date
  date=$(date +%Y%m%d)
  local i=1
  while git rev-parse --verify "failover-$date-$i" >/dev/null 2>&1; do
    i=$((i+1))
  done
  local branch="failover-$date-$i"
  if ! git diff --quiet || ! git diff --cached --quiet || [ -n "$(git ls-files --others --exclude-standard)" ]; then
    git add -A
    local tree
    tree=$(git write-tree)
    local commit
    commit=$(printf "Failover backup %s" "$(date -Is)" | git commit-tree "$tree" -p HEAD)
    git branch "$branch" "$commit"
    git reset --mixed HEAD
  else
    git branch "$branch"
  fi
  echo "Created failover branch $branch"
  backup_database_for_branch "$branch"
}

if [ -z "$FAILOVER_CREATED" ]; then
  create_failover_branch
fi

VENV_DIR="$SCRIPT_DIR/.venv"
PYTHON="$VENV_DIR/bin/python"
USE_SYSTEM_PYTHON=0
FORCE_REQUIREMENTS_INSTALL=0

LATEST=0
CLEAN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --latest)
      LATEST=1
      shift
      ;;
    --clean)
      CLEAN=1
      shift
      ;;
    *)
      break
      ;;
  esac
done

if [ ! -f "$PYTHON" ]; then
  if command -v python3 >/dev/null 2>&1; then
    if python3 -m venv "$VENV_DIR" >/dev/null 2>&1; then
      PYTHON="$VENV_DIR/bin/python"
      USE_SYSTEM_PYTHON=0
      FORCE_REQUIREMENTS_INSTALL=1
      echo "Virtual environment not found. Bootstrapping new virtual environment." >&2
    else
      PYTHON="$(command -v python3)"
      USE_SYSTEM_PYTHON=1
      echo "Virtual environment not found and automatic creation failed. Using system Python." >&2
    fi
  else
    echo "Python interpreter not found. Run ./install.sh first. Skipping." >&2
    exit 0
  fi
fi


# Ensure pip is available; attempt to install if missing
if ! "$PYTHON" -m pip --version >/dev/null 2>&1; then
  echo "pip not found in virtual environment. Attempting to install with ensurepip..." >&2
  if "$PYTHON" -m ensurepip --upgrade >/dev/null 2>&1 && \
     "$PYTHON" -m pip --version >/dev/null 2>&1; then
    :
  else
    echo "Failed to install pip automatically. On Debian/Ubuntu/WSL, ensure python3-venv is installed and rerun ./install.sh." >&2
    exit 1
  fi
fi


if [ "$CLEAN" -eq 1 ]; then
  find "$SCRIPT_DIR" -maxdepth 1 -name 'db*.sqlite3' -delete
fi

REQ_FILE="$SCRIPT_DIR/requirements.txt"
if [ -f "$REQ_FILE" ]; then
  MD5_FILE="$SCRIPT_DIR/requirements.md5"
  NEW_HASH=$(md5sum "$REQ_FILE" | awk '{print $1}')
  STORED_HASH=""
  [ -f "$MD5_FILE" ] && STORED_HASH=$(cat "$MD5_FILE")
  NEED_INSTALL=0
  if [ "$NEW_HASH" != "$STORED_HASH" ]; then
    NEED_INSTALL=1
  elif [ "$USE_SYSTEM_PYTHON" -eq 1 ]; then
    if ! "$PYTHON" - <<'PY' >/dev/null 2>&1
import importlib
import sys

try:
    importlib.import_module("django")
except ModuleNotFoundError:
    sys.exit(1)
PY
    then
      NEED_INSTALL=1
    fi
  fi
  if [ "$NEED_INSTALL" -eq 1 ]; then
    pip_args=()
    if [ "$USE_SYSTEM_PYTHON" -eq 1 ]; then
      pip_args+=(--user)
    fi
    "$PYTHON" -m pip install "${pip_args[@]}" -r "$REQ_FILE"
    echo "$NEW_HASH" > "$MD5_FILE"
  fi
elif [ -f "$REQ_FILE" ]; then
  MD5_FILE="$SCRIPT_DIR/requirements.system.md5"
  NEW_HASH=$(md5sum "$REQ_FILE" | awk '{print $1}')
  STORED_HASH=""
  [ -f "$MD5_FILE" ] && STORED_HASH=$(cat "$MD5_FILE")
  if [ "$NEW_HASH" != "$STORED_HASH" ]; then
    if "$PYTHON" -m pip install -r "$REQ_FILE"; then
      echo "$NEW_HASH" > "$MD5_FILE"
    else
      echo "Failed to install project requirements with system Python. Run ./install.sh." >&2
      exit 1
    fi
  fi
fi

ARGS=""
if [ "$LATEST" -eq 1 ]; then
  ARGS="$ARGS --latest"
fi
if [ "$CLEAN" -eq 1 ]; then
  ARGS="$ARGS --clean"
fi
"$PYTHON" env-refresh.py $ARGS database
