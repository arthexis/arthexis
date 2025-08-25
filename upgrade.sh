#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"

FORCE=0
if [[ "${1:-}" == "--latest" ]]; then
  FORCE=1
  shift
fi

# Determine current and remote versions
BRANCH=$(git rev-parse --abbrev-ref HEAD)
LOCAL_VERSION="0"
[ -f VERSION ] && LOCAL_VERSION=$(cat VERSION)

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
./stop.sh --all >/dev/null 2>&1 || true

# Pull latest changes
git pull --rebase

# Restore stashed changes
if [ "$STASHED" -eq 1 ]; then
  git stash pop || true
fi

# Ensure virtual environment is present
if [ ! -d .venv ]; then
  echo "Virtual environment not found. Run ./install.sh first." >&2
  exit 1
fi

# Refresh environment and restart service
./env-refresh.sh
./start.sh
