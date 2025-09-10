#!/usr/bin/env bash
# Release helper that bumps version, captures migration state, and tags the release.
set -euo pipefail

usage() {
  echo "Usage: $0 <version>"
}

case "${1:-}" in
  -h|--help)
    usage
    exit 0
    ;;
esac

if [ $# -ne 1 ]; then
  usage >&2
  exit 1
fi

VERSION="$1"

echo "$VERSION" > VERSION

python scripts/capture_migration_state.py "$VERSION"

git commit -am "Release $VERSION"
git tag -a "v$VERSION" -m "Release $VERSION"
git push origin main --tags
