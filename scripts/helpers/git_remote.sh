# shellcheck shell=bash

_arthexis_git_preconditions_met() {
  local repo_root="$1"

  if [ -z "$repo_root" ]; then
    return 1
  fi

  if ! command -v git >/dev/null 2>&1; then
    return 1
  fi

  if [ ! -d "$repo_root/.git" ]; then
    return 1
  fi

  return 0
}

arthexis_ensure_git_remote() {
  local repo_root="$1"
  local remote_name="$2"
  local remote_url="$3"

  if [ -z "$remote_name" ] || [ -z "$remote_url" ]; then
    return 0
  fi

  if ! _arthexis_git_preconditions_met "$repo_root"; then
    return 0
  fi

  if git -C "$repo_root" remote get-url "$remote_name" >/dev/null 2>&1; then
    return 0
  fi

  git -C "$repo_root" remote add "$remote_name" "$remote_url" >/dev/null || true
}

arthexis_ensure_upstream_remotes() {
  local repo_root="$1"
  local upstream_url="https://github.com/arthexis/arthexis"

  if ! _arthexis_git_preconditions_met "$repo_root"; then
    return 0
  fi

  arthexis_ensure_git_remote "$repo_root" "upstream" "$upstream_url"

  if ! git -C "$repo_root" remote get-url origin >/dev/null 2>&1; then
    arthexis_ensure_git_remote "$repo_root" "origin" "$upstream_url"
  fi
}
