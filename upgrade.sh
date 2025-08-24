#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"

# Stash local changes if any
STASHED=0
if ! git diff --quiet || ! git diff --cached --quiet || [ -n "$(git ls-files --others --exclude-standard)" ]; then
    echo "Warning: stashing local changes before upgrade" >&2
    git stash push -u -m "auto-upgrade $(date -Is)" >/dev/null || true
    STASHED=1
fi

# Pull latest changes
git pull --rebase

# Restore stashed changes
if [ "$STASHED" -eq 1 ]; then
    git stash pop || true
fi

# Update dependencies only if the virtualenv is present
if [ ! -d .venv ]; then
    echo "Virtual environment not found. Run ./install.sh first." >&2
    exit 1
fi

source .venv/bin/activate
REQ_FILE="requirements.txt"
MD5_FILE="requirements.md5"
NEW_HASH=$(md5sum "$REQ_FILE" | awk '{print $1}')
STORED_HASH=""
[ -f "$MD5_FILE" ] && STORED_HASH=$(cat "$MD5_FILE")
if [ "$NEW_HASH" != "$STORED_HASH" ]; then
    pip install -r "$REQ_FILE"
    echo "$NEW_HASH" > "$MD5_FILE"
else
    echo "Requirements unchanged. Skipping installation."
fi
deactivate
