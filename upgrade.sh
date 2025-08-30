#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$BASE_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1
cd "$BASE_DIR"

FORCE=0
CLEAN_DB=0
NO_RESTART=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --latest)
      FORCE=1
      shift
      ;;
    --clean-db)
      CLEAN_DB=1
      shift
      ;;
    --no-restart)
      NO_RESTART=1
      shift
      ;;
    *)
      break
      ;;
  esac
done

# Determine current and remote versions
BRANCH=$(git rev-parse --abbrev-ref HEAD)
LOCAL_VERSION="0"
[ -f VERSION ] && LOCAL_VERSION=$(cat VERSION)

echo "Checking repository for updates..."
git fetch origin "$BRANCH"
REMOTE_VERSION="$LOCAL_VERSION"
if git cat-file -e "origin/$BRANCH:VERSION" 2>/dev/null; then
  REMOTE_VERSION=$(git show "origin/$BRANCH:VERSION" | tr -d '\r')
fi

if [[ $FORCE -ne 1 && "$LOCAL_VERSION" == "$REMOTE_VERSION" ]]; then
  echo "Already up-to-date (version $LOCAL_VERSION)"
  exit 0
fi

# Stash local changes if any
STASHED=0
if ! git diff --quiet || ! git diff --cached --quiet || [ -n "$(git ls-files --others --exclude-standard)" ]; then
  echo "Warning: stashing local changes before upgrade" >&2
  git stash push -u -m "auto-upgrade $(date -Is)" >/dev/null || true
  STASHED=1
fi

# Stop running instance (if any)
if [[ $NO_RESTART -eq 0 ]]; then
  echo "Stopping running instance..."
  ./stop.sh --all >/dev/null 2>&1 || true
fi

# Pull latest changes
echo "Pulling latest changes..."
git pull --rebase

# Restore stashed changes
if [ "$STASHED" -eq 1 ]; then
  echo "Restoring local changes..."
  git stash pop || true
fi

# Ensure virtual environment is present
if [ ! -d .venv ]; then
  echo "Virtual environment not found. Run ./install.sh first." >&2
  exit 1
fi

# Remove existing database if requested
if [ "$CLEAN_DB" -eq 1 ]; then
  DB_FILE="db.sqlite3"
  rm -f "$DB_FILE"
fi

# Refresh environment and restart service
ENV_ARGS=""
if [[ $FORCE -eq 1 ]]; then
  ENV_ARGS="--latest"
fi
echo "Refreshing environment..."
./env-refresh.sh $ENV_ARGS
if [[ $NO_RESTART -eq 0 ]]; then
  echo "Restarting services..."
  ./start.sh
fi
