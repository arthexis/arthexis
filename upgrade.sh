#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"

# Stash local changes if any
STASHED=0
if ! git diff --quiet || ! git diff --cached --quiet; then
    git stash push -u -m "auto-upgrade $(date -Is)" || true
    STASHED=1
fi

# Pull latest changes
git pull --rebase

# Restore stashed changes
if [ "$STASHED" -eq 1 ]; then
    git stash pop || true
fi

# Update dependencies if virtualenv exists
if [ -d .venv ]; then
    source .venv/bin/activate
    pip install -r requirements.txt
    deactivate
fi
