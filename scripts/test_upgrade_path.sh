#!/usr/bin/env bash
# Test migrating from the previous release tag to the current code.
set -euo pipefail

usage() {
  echo "Usage: $0 [-h|--help]"
}

case "${1:-}" in
  -h|--help)
    usage
    exit 0
    ;;
esac

if [ $# -ne 0 ]; then
  usage >&2
  exit 1
fi

PREV_TAG=$(git describe --tags --abbrev=0 HEAD^)
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
