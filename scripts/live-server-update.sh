#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT"

if [ -x "$REPO_ROOT/stop.sh" ]; then
  echo "Stopping existing development server (if running)..."
  if ! "$REPO_ROOT/stop.sh"; then
    echo "The stop script exited with a non-zero status; continuing anyway." >&2
  fi
fi

if ! git remote get-url upstream >/dev/null 2>&1; then
  echo "No 'upstream' remote configured. Skipping fetch and pull."
  exit 0
fi

current_branch="$(git rev-parse --abbrev-ref HEAD)"
if [ -z "$current_branch" ]; then
  echo "Unable to determine the current branch. Skipping fetch and pull." >&2
  exit 0
fi

echo "Fetching updates from upstream..."
git fetch upstream

echo "Checking for upstream branch $current_branch..."
if git show-ref --verify --quiet "refs/remotes/upstream/${current_branch}"; then
  echo "Pulling latest commits for ${current_branch}..."
  git pull --ff-only upstream "$current_branch"
  REFRESH_SCRIPT="$REPO_ROOT/env-refresh.sh"
  if [ -x "$REFRESH_SCRIPT" ]; then
    echo "Refreshing environment with env-refresh.sh --latest..."
    "$REFRESH_SCRIPT" --latest
  elif [ -f "$REFRESH_SCRIPT" ]; then
    echo "env-refresh.sh is not marked executable. Attempting to run with bash." >&2
    bash "$REFRESH_SCRIPT" --latest
  else
    echo "env-refresh.sh not found. Skipping environment refresh." >&2
  fi

else
  echo "No matching upstream branch for ${current_branch}. Skipping pull."
fi
