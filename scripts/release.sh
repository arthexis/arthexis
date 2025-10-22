#!/usr/bin/env bash
# Release helper that bumps version, captures migration state, creates a source archive, and tags the release.
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "Usage: release.sh <version>" >&2
  exit 1
fi

VERSION="$1"

PREVIOUS_VERSION=$(git show HEAD:VERSION 2>/dev/null || true)

maybe_create_maintenance_branch() {
  local previous="$1"
  local current="$2"

  if [[ -z "$previous" ]]; then
    echo "Unable to determine previous version; skipping maintenance branch creation." >&2
    return
  fi

  local prev_major prev_minor curr_major curr_minor
  local IFS='.'
  read -r prev_major prev_minor _ <<<"$previous"
  read -r curr_major curr_minor _ <<<"$current"

  if [[ -z "$prev_major" || -z "$prev_minor" || -z "$curr_major" || -z "$curr_minor" ]]; then
    echo "Unable to parse version numbers; skipping maintenance branch creation." >&2
    return
  fi

  if [[ "$prev_major" != "$curr_major" || "$prev_minor" == "$curr_minor" ]]; then
    return
  fi

  local maintenance_branch="release/v${prev_major}.${prev_minor}"

  if git show-ref --verify --quiet "refs/heads/${maintenance_branch}"; then
    echo "Maintenance branch ${maintenance_branch} already exists locally." >&2
  else
    git branch "$maintenance_branch"
    echo "Created maintenance branch ${maintenance_branch} from $(git rev-parse --short HEAD)."
  fi

  if git ls-remote --exit-code --heads origin "$maintenance_branch" >/dev/null 2>&1; then
    echo "Maintenance branch ${maintenance_branch} already exists on origin." >&2
  else
    git push origin "$maintenance_branch"
  fi
}

# Ensure the repository is clean before making any changes.
if [ -n "$(git status --porcelain)" ]; then
  echo "Working tree or index is dirty; please commit or stash changes before releasing." >&2
  exit 1
fi

maybe_create_maintenance_branch "$PREVIOUS_VERSION" "$VERSION"

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
