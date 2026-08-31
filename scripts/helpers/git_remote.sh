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

arthexis_git_url_uses_github_ssh() {
  local remote_url="$1"

  [[ "$remote_url" == "git@github.com:"* || "$remote_url" == "ssh://git@github.com/"* ]]
}

arthexis_git_ssh_command() {
  local repo_root="${1:-}"
  local ssh_command="${GIT_SSH_COMMAND:-}"

  if [ -z "$ssh_command" ] && [ -n "$repo_root" ]; then
    ssh_command="$(git -C "$repo_root" config --get core.sshCommand 2>/dev/null || true)"
  fi

  if [ -z "$ssh_command" ]; then
    ssh_command="ssh"
  fi

  printf '%s\n' "$ssh_command"
}

arthexis_git_for_remote_url_with_repo() {
  local remote_url="$1"
  local repo_root="$2"
  shift 2

  if arthexis_git_url_uses_github_ssh "$remote_url"; then
    GIT_SSH_COMMAND="$(arthexis_git_ssh_command "$repo_root")" git "$@"
  else
    git "$@"
  fi
}

arthexis_git_for_remote_url() {
  local remote_url="$1"
  shift

  arthexis_git_for_remote_url_with_repo "$remote_url" "" "$@"
}

arthexis_git_for_remote() {
  local repo_root="$1"
  local remote_name="$2"
  local remote_url=""
  shift 2

  remote_url="$(git -C "$repo_root" remote get-url "$remote_name" 2>/dev/null || echo "")"
  arthexis_git_for_remote_url_with_repo "$remote_url" "$repo_root" -C "$repo_root" "$@"
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
  local upstream_url="git@github.com:arthexis/arthexis.git"

  if ! _arthexis_git_preconditions_met "$repo_root"; then
    return 0
  fi

  arthexis_ensure_git_remote "$repo_root" "upstream" "$upstream_url"

  if ! git -C "$repo_root" remote get-url origin >/dev/null 2>&1; then
    arthexis_ensure_git_remote "$repo_root" "origin" "$upstream_url"
  fi
}
