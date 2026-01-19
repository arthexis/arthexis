# shellcheck shell=bash

arthexis_ensure_git_remote() {
  local repo_root="$1"
  local remote_name="$2"
  local remote_url="$3"

  if [ -z "$repo_root" ] || [ -z "$remote_name" ] || [ -z "$remote_url" ]; then
    return 0
  fi

  if ! command -v git >/dev/null 2>&1; then
    return 0
  fi

  if [ ! -d "$repo_root/.git" ]; then
    return 0
  fi

  if git -C "$repo_root" remote get-url "$remote_name" >/dev/null 2>&1; then
    return 0
  fi

  git -C "$repo_root" remote add "$remote_name" "$remote_url" >/dev/null 2>&1 || true
}

arthexis_ensure_upstream_remotes() {
  local repo_root="$1"
  local upstream_url="https://github.com/arthexis/arthexis"

  if ! command -v git >/dev/null 2>&1; then
    return 0
  fi

  if [ ! -d "$repo_root/.git" ]; then
    return 0
  fi

  arthexis_ensure_git_remote "$repo_root" "upstream" "$upstream_url"

  if ! git -C "$repo_root" remote get-url origin >/dev/null 2>&1; then
    arthexis_ensure_git_remote "$repo_root" "origin" "$upstream_url"
  fi
}
