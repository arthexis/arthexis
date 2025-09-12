#!/usr/bin/env bash
# Test migrating from the previous release tag to the current code.
set -euo pipefail

PREV_TAG=$(git describe --tags --abbrev=0 HEAD^ 2>/dev/null || true)
if [[ -z "$PREV_TAG" ]]; then
  echo "No previous release tag found; skipping upgrade path test." >&2
  exit 0
fi

WORKTREE_DIR=$(mktemp -d)

cleanup() {
  git worktree remove "$WORKTREE_DIR" --force >/dev/null 2>&1 || true
}
trap cleanup EXIT

git worktree add "$WORKTREE_DIR" "$PREV_TAG"

pushd "$WORKTREE_DIR" >/dev/null
python manage.py migrate --noinput
popd >/dev/null

python manage.py migrate --noinput
