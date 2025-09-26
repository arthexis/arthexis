#!/usr/bin/env bash
set -euo pipefail

# Generate CHANGELOG.rst from commit messages.
# Usage: scripts/generate-changelog.sh [starting-tag]
# If starting-tag is omitted, the last tag is used.

start_tag="${1:-$(git describe --tags --abbrev=0 2>/dev/null || echo '')}"

if [ -n "$start_tag" ]; then
  range="$start_tag..HEAD"
else
  range="HEAD"
fi

if [ -f CHANGELOG.rst ]; then
  previous=$(tail -n +7 CHANGELOG.rst)
else
  previous=""
fi

{
  echo "Changelog"
  echo "========="
  echo
  echo "Unreleased"
  echo "----------"
  echo
  # Filter out commit subjects that are a single word to keep the changelog informative.
  git log $range --no-merges --pretty=format:"- %h %s" | awk 'NF > 3'
  if [ -n "$previous" ]; then
    echo
    echo "$previous"
  fi
} > CHANGELOG.rst
