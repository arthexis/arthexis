#!/usr/bin/env bash

arthexis_post_upgrade_hook_dir() {
  local base_dir="$1"
  local lock_dir="${2:-$base_dir/.locks}"

  printf '%s\n' "${ARTHEXIS_POST_UPGRADE_HOOK_DIR:-$lock_dir/post-upgrade.d}"
}

arthexis_has_post_upgrade_hooks() {
  local base_dir="$1"
  local lock_dir="${2:-$base_dir/.locks}"
  local hook_dir
  hook_dir="$(arthexis_post_upgrade_hook_dir "$base_dir" "$lock_dir")"

  if [ ! -d "$hook_dir" ]; then
    return 1
  fi

  local candidate
  for candidate in "$hook_dir"/*; do
    [ -e "$candidate" ] || continue
    [ -f "$candidate" ] || continue
    [ -x "$candidate" ] || continue
    return 0
  done

  return 1
}

arthexis_run_post_upgrade_hooks() {
  local base_dir="$1"
  local lock_dir="${2:-$base_dir/.locks}"
  local hook_dir
  hook_dir="$(arthexis_post_upgrade_hook_dir "$base_dir" "$lock_dir")"

  if [ ! -d "$hook_dir" ]; then
    return 0
  fi

  local -a hooks=()
  local candidate
  for candidate in "$hook_dir"/*; do
    [ -e "$candidate" ] || continue
    [ -f "$candidate" ] || continue
    [ -x "$candidate" ] || continue
    hooks+=("$candidate")
  done

  if [ ${#hooks[@]} -eq 0 ]; then
    return 0
  fi

  local hook hook_name status
  for hook in "${hooks[@]}"; do
    hook_name="${hook##*/}"
    echo "Running post-upgrade hook: $hook_name"

    if (
      export ARTHEXIS_BASE_DIR="$base_dir"
      export ARTHEXIS_LOCK_DIR="$lock_dir"
      export ARTHEXIS_POST_UPGRADE_HOOK="$hook"
      export ARTHEXIS_PREVIOUS_REVISION="${UPGRADE_INITIAL_REVISION:-${LOCAL_REVISION:-}}"
      export ARTHEXIS_CURRENT_REVISION="${CURRENT_REVISION:-${REMOTE_REVISION:-}}"
      export ARTHEXIS_TARGET_REVISION="${REMOTE_REVISION:-}"
      export ARTHEXIS_INITIAL_VERSION="${UPGRADE_INITIAL_VERSION:-${LOCAL_VERSION:-}}"
      export ARTHEXIS_TARGET_VERSION="${REMOTE_VERSION:-}"
      export ARTHEXIS_UPGRADE_CHANNEL="${CHANNEL:-}"
      export ARTHEXIS_SERVICE_NAME="${SERVICE_NAME:-}"
      export ARTHEXIS_PYTHON_BIN="${PYTHON_BIN:-}"
      "$hook"
    ); then
      if [ -e "$hook" ]; then
        if ! rm -f -- "$hook"; then
          echo "Post-upgrade hook completed but could not be removed: $hook_name" >&2
          return 1
        fi
        echo "Removed completed post-upgrade hook: $hook_name"
      else
        echo "Post-upgrade hook removed itself: $hook_name"
      fi
      continue
    else
      status=$?
      echo "Post-upgrade hook failed and was left for retry: $hook_name" >&2
      return "$status"
    fi
  done
}
