#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -z "$SCRIPT_DIR" ] || [ "$SCRIPT_DIR" = "/" ]; then
  echo "Refusing to run from root directory." >&2
  exit 1
fi
cd "$SCRIPT_DIR"
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1

VENV_DIR="$SCRIPT_DIR/.venv"
PYTHON="$VENV_DIR/bin/python"

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
  echo "Virtual environment not found. Run ./install.sh first." >&2
  exit 1
fi


if [ "$CLEAN" -eq 1 ]; then
  DB_FILE="$SCRIPT_DIR/db.sqlite3"
  if [ "$(basename "$DB_FILE")" != "db.sqlite3" ]; then
    echo "Unexpected database file: $DB_FILE" >&2
    exit 1
  fi
  case "$DB_FILE" in
    "$SCRIPT_DIR"/*) ;;
    *)
      echo "Database path outside repository: $DB_FILE" >&2
      exit 1
      ;;
  esac
  if [ -f "$DB_FILE" ]; then
    BACKUP_DIR="$SCRIPT_DIR/backups"
    mkdir -p "$BACKUP_DIR"
    VERSION="unknown"
    [ -f "$SCRIPT_DIR/VERSION" ] && VERSION="$(cat "$SCRIPT_DIR/VERSION")"
    REVISION="unknown"
    REVISION="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
    STAMP="$(date +%Y%m%d%H%M%S)"
    cp "$DB_FILE" "$BACKUP_DIR/db.sqlite3.${VERSION}.${REVISION}.${STAMP}.bak"
    rm "$DB_FILE"
  fi
fi

REQ_FILE="$SCRIPT_DIR/requirements.txt"
if [ -f "$REQ_FILE" ]; then
  MD5_FILE="$SCRIPT_DIR/requirements.md5"
  NEW_HASH=$(md5sum "$REQ_FILE" | awk '{print $1}')
  STORED_HASH=""
  [ -f "$MD5_FILE" ] && STORED_HASH=$(cat "$MD5_FILE")
  if [ "$NEW_HASH" != "$STORED_HASH" ]; then
    "$PYTHON" -m pip install -r "$REQ_FILE"
    echo "$NEW_HASH" > "$MD5_FILE"
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
