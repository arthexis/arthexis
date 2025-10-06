#!/usr/bin/env bash
# Release helper that bumps version, captures migration state, creates a source archive, and tags the release.
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "Usage: release.sh <version>" >&2
  exit 1
fi

VERSION="$1"

# Ensure the repository is clean before making any changes.
if [ -n "$(git status --porcelain)" ]; then
  echo "Working tree or index is dirty; please commit or stash changes before releasing." >&2
  exit 1
fi

echo "$VERSION" > VERSION

python scripts/capture_migration_state.py "$VERSION"

# Stage release metadata before committing. capture_migration_state.py adds the
# generated files, but we explicitly stage the directory to ensure every
# artifact (including the directory itself) is captured in the commit.
git add VERSION "releases/${VERSION}"

git commit -m "Release $VERSION"

# Build the source archive from the freshly created release commit so the
# archive reflects the published version.
git archive --format=tar.gz -o "releases/${VERSION}/source.tar.gz" HEAD

# Amend the release commit to include the generated source archive.
git add "releases/${VERSION}/source.tar.gz"
git commit --amend --no-edit

git tag -a "v$VERSION" -m "Release $VERSION"
git push origin main --tags
