#!/usr/bin/env bash
# Release helper that bumps version, captures migration state, creates a source archive, and tags the release.
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "Usage: release.sh <version>" >&2
  exit 1
fi

VERSION="$1"

echo "$VERSION" > VERSION

python scripts/capture_migration_state.py "$VERSION"

git archive --format=tar.gz -o "releases/${VERSION}/source.tar.gz" HEAD

git commit -am "Release $VERSION"
git tag -a "v$VERSION" -m "Release $VERSION"
git push origin main --tags
