#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT"

if [ -x "$REPO_ROOT/stop.sh" ]; then
  echo "Stopping existing development server (if running)..."
  if ! "$REPO_ROOT/stop.sh"; then
    echo "Live server update aborted because stop.sh detected active charging sessions. Resolve the sessions or run '$REPO_ROOT/stop.sh --force' during a maintenance window before retrying." >&2
    exit 1
  fi
fi

REFRESH_SCRIPT="$REPO_ROOT/env-refresh.sh"
DEFAULT_REMOTE="origin"
DEFAULT_BRANCH="main"

current_branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
if [ "$current_branch" = "HEAD" ]; then
  current_branch=""
fi

sync_remote_branch() {
  local remote="$1"
  local branch="$2"

  if ! git remote get-url "$remote" >/dev/null 2>&1; then
    echo "Remote '$remote' not configured. Skipping fetch and pull."
    return 1
  fi

  echo "Fetching updates from ${remote}/${branch}..."
  if ! git fetch "$remote" "$branch"; then
    echo "Failed to fetch from ${remote}/${branch}." >&2
    return 1
  fi

  if ! git show-ref --verify --quiet "refs/remotes/${remote}/${branch}"; then
    echo "No matching ${remote}/${branch} found. Skipping pull."
    return 1
  fi

  if [ -z "$current_branch" ] || [ "$branch" != "$current_branch" ]; then
    if [ -z "$current_branch" ]; then
      echo "Detached HEAD state detected; skipping pull for ${remote}/${branch}."
    else
      echo "Current branch '$current_branch' does not match '$branch'. Skipping pull."
    fi
    return 0
  fi

  echo "Pulling latest commits for ${branch} from ${remote}..."
  if ! git pull --ff-only "$remote" "$branch"; then
    echo "Failed to pull from ${remote}/${branch}." >&2
    return 1
  fi

  return 0
}

synced="false"

if [ -n "$current_branch" ] && git remote get-url upstream >/dev/null 2>&1; then
  echo "Attempting to update from upstream/${current_branch}..."
  if sync_remote_branch "upstream" "$current_branch"; then
    synced="true"
  else
    echo "Unable to update from upstream/${current_branch}. Falling back to ${DEFAULT_REMOTE}/${DEFAULT_BRANCH}."
  fi
else
  if [ -z "$current_branch" ]; then
    echo "Unable to determine the current branch for upstream sync."
  else
    echo "No 'upstream' remote configured."
  fi
  echo "Using default upstream ${DEFAULT_REMOTE}/${DEFAULT_BRANCH}."
fi

if [ "$synced" != "true" ]; then
  if ! sync_remote_branch "$DEFAULT_REMOTE" "$DEFAULT_BRANCH"; then
    echo "Skipping pull; ${DEFAULT_REMOTE}/${DEFAULT_BRANCH} could not be updated."
  fi
fi

if [ -x "$REFRESH_SCRIPT" ]; then
  echo "Refreshing environment with env-refresh.sh --latest..."
  "$REFRESH_SCRIPT" --latest
elif [ -f "$REFRESH_SCRIPT" ]; then
  echo "env-refresh.sh is not marked executable. Attempting to run with bash." >&2
  bash "$REFRESH_SCRIPT" --latest
else
  echo "env-refresh.sh not found. Skipping environment refresh." >&2
fi
