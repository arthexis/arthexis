#!/usr/bin/env bash
set -eE

# Initialize logging and helper functions shared across upgrade steps.
BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/helpers/git_remote.sh
. "$BASE_DIR/scripts/helpers/git_remote.sh"

github_raw_version_url_for_revision() {
  local revision="$1"
  local remote_url=""
  local owner_repo=""

  remote_url="$(git remote get-url origin 2>/dev/null || echo "")"
  case "$remote_url" in
    https://github.com/*)
      owner_repo="${remote_url#https://github.com/}"
      ;;
    http://github.com/*)
      owner_repo="${remote_url#http://github.com/}"
      ;;
    git@github.com:*)
      owner_repo="${remote_url#git@github.com:}"
      ;;
    ssh://git@github.com/*)
      owner_repo="${remote_url#ssh://git@github.com/}"
      ;;
    *)
      return 1
      ;;
  esac

  owner_repo="${owner_repo%.git}"
  owner_repo="${owner_repo%/}"
  if [[ -z "$owner_repo" ]]; then
    return 1
  fi

  printf 'https://raw.githubusercontent.com/%s/%s/VERSION\n' "$owner_repo" "$revision"
}

read_remote_version_without_ref_update() {
  local revision="$1"
  local remote_version=""
  local raw_url=""

  if git cat-file -e "$revision:VERSION" 2>/dev/null; then
    git show "$revision:VERSION" | tr -d '\r\n'
    return 0
  fi

  if remote_version="$(read_remote_version_from_temporary_fetch "$revision")"; then
    printf '%s\n' "$remote_version"
    return 0
  fi

  if ! command -v curl >/dev/null 2>&1; then
    return 1
  fi

  raw_url="$(github_raw_version_url_for_revision "$revision")" || return 1
  remote_version="$(curl -fsSL --max-time 20 "$raw_url" 2>/dev/null | tr -d '\r\n')" || return 1
  if [[ -z "$remote_version" ]]; then
    return 1
  fi

  printf '%s\n' "$remote_version"
}

read_remote_version_from_temporary_fetch() {
  local revision="$1"
  local remote_url=""
  local remote_version=""
  local status=1
  local tmp_dir=""

  remote_url="$(git remote get-url origin 2>/dev/null || echo "")"
  if [[ -z "$remote_url" ]]; then
    return 1
  fi

  tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/arthexis-version.XXXXXX")" || return 1
  if git init -q "$tmp_dir" &&
    arthexis_git_for_remote_url_with_repo "$remote_url" "$BASE_DIR" -C "$tmp_dir" fetch --depth=1 --no-tags "$remote_url" "$revision" >/dev/null 2>&1; then
    remote_version="$(git -C "$tmp_dir" show FETCH_HEAD:VERSION 2>/dev/null | tr -d '\r\n')" || status=1
    if [[ -n "$remote_version" ]]; then
      status=0
    fi
  fi

  rm -rf "$tmp_dir"
  if [[ $status -ne 0 ]]; then
    return 1
  fi

  printf '%s\n' "$remote_version"
}

read_remote_tag_revision_without_ref_update() {
  local target_tag="$1"
  local tag_ref="refs/tags/$target_tag"
  local tag_listing=""
  local target_tag_revision=""

  tag_listing="$(arthexis_git_for_remote "$BASE_DIR" origin ls-remote --tags origin "$tag_ref" "$tag_ref^{}" 2>/dev/null || echo "")"
  target_tag_revision="$(printf '%s\n' "$tag_listing" | awk -v ref="$tag_ref^{}" '$2 == ref {print $1; exit}')"
  if [[ -z "$target_tag_revision" ]]; then
    target_tag_revision="$(printf '%s\n' "$tag_listing" | awk -v ref="$tag_ref" '$2 == ref {print $1; exit}')"
  fi

  if [[ -z "$target_tag_revision" ]]; then
    return 1
  fi

  printf '%s\n' "$target_tag_revision"
}

early_has_post_upgrade_hooks() {
  local lock_dir="$BASE_DIR/.locks"
  local hook_dir="${ARTHEXIS_POST_UPGRADE_HOOK_DIR:-$lock_dir/post-upgrade.d}"
  local candidate=""

  if [ ! -d "$hook_dir" ]; then
    return 1
  fi

  for candidate in "$hook_dir"/*; do
    [ -e "$candidate" ] || continue
    [ -f "$candidate" ] || continue
    [ -x "$candidate" ] || continue
    return 0
  done

  return 1
}

early_self_update_rerun_pending() {
  [ -f "$BASE_DIR/.locks/upgrade_rerun_required.lck" ]
}

early_pre_check_report_and_exit() {
  local pre_check=0
  local channel="stable"
  local local_only=0
  local force_upgrade=0
  local revert_upgrade=0
  local requested_branch=""
  local target_version=""
  local target_revision=""
  local target_tag=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --latest|--unstable|-l|-t)
        channel="unstable"
        shift
        ;;
      --stable|--lts)
        channel="stable"
        shift
        ;;
      --normal|--regular)
        channel="regular"
        shift
        ;;
      --force|-f)
        force_upgrade=1
        shift
        ;;
      --pre-check)
        pre_check=1
        shift
        ;;
      --no-check)
        pre_check=0
        shift
        ;;
      --local)
        local_only=1
        shift
        ;;
      --revert)
        revert_upgrade=1
        shift
        ;;
      --branch)
        if [[ -z "${2:-}" ]]; then
          echo "--branch requires an argument" >&2
          exit 1
        fi
        requested_branch="$2"
        shift 2
        ;;
      --main)
        requested_branch="main"
        shift
        ;;
      --target-version)
        if [[ -z "${2:-}" ]]; then
          echo "--target-version requires an argument" >&2
          exit 1
        fi
        target_version="$2"
        shift 2
        ;;
      --target-revision)
        if [[ -z "${2:-}" ]]; then
          echo "--target-revision requires an argument" >&2
          exit 1
        fi
        target_revision="$2"
        shift 2
        ;;
      --target-tag)
        if [[ -z "${2:-}" ]]; then
          echo "--target-tag requires an argument" >&2
          exit 1
        fi
        target_tag="$2"
        shift 2
        ;;
      --confirm|--stash|--force-refresh|--clean|--migrate|--reconcile|--no-start|--no-restart|--stop|--start|-s|--no-warn|--clear-logs|--clear-work|--detached|--check)
        shift
        ;;
      *)
        echo "Unknown option: $1" >&2
        exit 1
        ;;
    esac
  done

  [[ $pre_check -eq 1 ]] || return 0

  if early_self_update_rerun_pending; then
    echo "Detected pending upgrade.sh self-update rerun; continuing upgrade recovery instead of running read-only pre-check."
    return 0
  fi

  if [[ "$channel" == "unstable" && ( -n "$target_version" || -n "$target_revision" || -n "$target_tag" ) ]]; then
    echo "Pinned release targets cannot be combined with --latest/--unstable." >&2
    exit 1
  fi

  if [[ -n "$target_version" && -z "$target_revision" && -z "$target_tag" ]]; then
    echo "Pinned version pre-check requires --target-tag or --target-revision." >&2
    exit 1
  fi

  cd "$BASE_DIR" || exit 1

  local local_version="0"
  local local_revision=""
  local branch="$requested_branch"
  local local_ref="HEAD"
  local post_upgrade_hooks_pending=0
  local requested_branch_remote_only=0
  if [[ -z "$branch" ]]; then
    branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")"
    if [[ "$branch" == "HEAD" || -z "$branch" ]]; then
      branch="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || echo "origin/main")"
      branch="${branch#origin/}"
    fi
  elif git show-ref --verify --quiet "refs/heads/$branch"; then
    local_ref="refs/heads/$branch"
  elif git show-ref --verify --quiet "refs/remotes/origin/$branch"; then
    local_ref="refs/remotes/origin/$branch"
    requested_branch_remote_only=1
  fi
  if git cat-file -e "$local_ref:VERSION" 2>/dev/null; then
    local_version="$(git show "$local_ref:VERSION" | tr -d '\r\n')"
  elif [ -f VERSION ]; then
    local_version=$(tr -d '\r\n' < VERSION)
  fi
  local_revision="$(git rev-parse "$local_ref" 2>/dev/null || echo "")"
  if early_has_post_upgrade_hooks; then
    post_upgrade_hooks_pending=1
  fi

  echo "Upgrade pre-check complete; no changes applied."
  if [[ $requested_branch_remote_only -eq 1 ]]; then
    echo "Upgrade would create local branch $branch from origin/$branch."
  fi

  if [[ $revert_upgrade -eq 1 ]]; then
    local revert_lock="$BASE_DIR/.locks/upgrade_revert_revision.lck"
    local revert_target=""
    [ -f "$revert_lock" ] && revert_target="$(tr -d '\r\n' < "$revert_lock")"
    if [[ -z "$revert_target" ]]; then
      echo "No previous upgrade revision is recorded; cannot revert." >&2
      exit 1
    fi
    echo "Upgrade would revert working tree to revision $revert_target."
    exit 0
  fi

  if [[ -n "$target_revision" && -z "$target_tag" ]]; then
    local resolved_target_revision=""
    local target_revision_version=""
    resolved_target_revision="$(git rev-parse --verify "${target_revision}^{commit}" 2>/dev/null || echo "")"
    if [[ -z "$resolved_target_revision" ]]; then
      echo "Error: pinned release target $target_revision is not available locally." >&2
      exit 1
    fi
    if ! git cat-file -e "$resolved_target_revision:VERSION" 2>/dev/null; then
      echo "Error: pinned release target $resolved_target_revision does not contain VERSION." >&2
      exit 1
    fi
    target_revision_version="$(git show "$resolved_target_revision:VERSION" | tr -d '\r\n')"
    if [[ -n "$target_version" && "$target_revision_version" != "$target_version" ]]; then
      echo "Error: pinned release target VERSION is $target_revision_version, expected $target_version." >&2
      exit 1
    fi
    if [[ "$local_revision" == "$resolved_target_revision" ]]; then
      echo "No upgrade required."
    else
      echo "Upgrade would align working tree to revision $resolved_target_revision."
    fi
    exit 0
  fi

  if [[ -n "$target_tag" ]]; then
    local target_tag_revision=""
    local target_tag_version=""
    target_tag_revision="$(read_remote_tag_revision_without_ref_update "$target_tag" || echo "")"
    if [[ -z "$target_tag_revision" ]]; then
      echo "Pinned release tag $target_tag was not found on origin." >&2
      exit 1
    fi
    if [[ -n "$target_revision" && "$target_tag_revision" != "$target_revision" ]]; then
      echo "Error: pinned release target resolved to $target_tag_revision, expected $target_revision." >&2
      exit 1
    fi
    target_tag_version="$(read_remote_version_without_ref_update "$target_tag_revision" || echo "")"
    if [[ -z "$target_tag_version" ]]; then
      echo "Unable to inspect tag $target_tag VERSION without updating refs." >&2
      exit 1
    fi
    if [[ -n "$target_version" && "$target_tag_version" != "$target_version" ]]; then
      echo "Error: pinned release target VERSION is $target_tag_version, expected $target_version." >&2
      exit 1
    fi
    if [[ "$local_revision" == "$target_tag_revision" ]]; then
      echo "No upgrade required."
    else
      echo "Upgrade would align working tree to tag $target_tag ($target_tag_revision)."
    fi
    exit 0
  fi

  if [[ $local_only -eq 1 ]]; then
    echo "Upgrade would run a local environment refresh."
    exit 0
  fi

  local remote_revision=""
  local remote_version="$local_version"
  local remote_version_available=0
  remote_revision="$(arthexis_git_for_remote "$BASE_DIR" origin ls-remote --heads origin "$branch" 2>/dev/null | awk 'NR == 1 {print $1}')"
  if [[ -z "$remote_revision" ]]; then
    echo "Unable to inspect origin/$branch without updating refs." >&2
    exit 1
  fi
  if [[ -n "$local_revision" && "$local_revision" != "$remote_revision" ]]; then
    if remote_version="$(read_remote_version_without_ref_update "$remote_revision")"; then
      remote_version_available=1
    fi
  fi

  if [[ -z "$local_revision" || "$local_revision" == "$remote_revision" ]]; then
    if [[ $force_upgrade -eq 1 ]]; then
      echo "Upgrade would run because --force was provided."
    elif [[ $post_upgrade_hooks_pending -eq 1 ]]; then
      echo "Upgrade would retry pending post-upgrade hooks."
    elif [[ $requested_branch_remote_only -eq 1 ]]; then
      :
    else
      echo "No upgrade required."
    fi
  elif [[ "$channel" == "unstable" ]]; then
    echo "Upgrade would update revision $local_revision -> $remote_revision."
  elif [[ $remote_version_available -eq 1 && "$local_version" != "$remote_version" ]]; then
    echo "Upgrade would update version $local_version -> $remote_version."
  elif [[ $force_upgrade -eq 1 ]]; then
    echo "Upgrade would run because --force was provided."
  elif [[ $post_upgrade_hooks_pending -eq 1 ]]; then
    echo "Upgrade would retry pending post-upgrade hooks."
  elif [[ $remote_version_available -eq 0 ]]; then
    echo "Unable to inspect origin/$branch VERSION without updating refs." >&2
    exit 1
  else
    echo "Updates detected for version $local_version, but --latest is required to apply them."
    echo "Re-run upgrade.sh with --latest to migrate to the newest changes."
  fi

  exit 0
}

early_pre_check_report_and_exit "$@"

export TZ="${TZ:-America/Monterrey}"
PIP_INSTALL_HELPER="$BASE_DIR/scripts/helpers/pip_install.py"
UPGRADE_STARTED_AT=$(date +%s)
UPGRADE_DURATION_LOCK="$BASE_DIR/.locks/upgrade_duration.lck"
# Track upgrade script changes triggered by git pull so the newer version can be re-run.
UPGRADE_SCRIPT_PATH="$BASE_DIR/upgrade.sh"
INITIAL_UPGRADE_HASH=""
UPGRADE_RERUN_EXIT_CODE=3
UPGRADE_STASH_REF=""
UPGRADE_STASH_CREATED=0
if [ -f "$UPGRADE_SCRIPT_PATH" ]; then
  INITIAL_UPGRADE_HASH="$(sha256sum "$UPGRADE_SCRIPT_PATH" | awk '{print $1}')"
fi
# shellcheck source=scripts/helpers/logging.sh
. "$BASE_DIR/scripts/helpers/logging.sh"
# shellcheck source=scripts/helpers/common.sh
. "$BASE_DIR/scripts/helpers/common.sh"
# Record upgrade lifecycle in the startup report for visibility in admin reports.
UPGRADE_SCRIPT_NAME="$(basename "$0")"
arthexis_log_startup_event "$BASE_DIR" "$UPGRADE_SCRIPT_NAME" "start" "invoked"
UPGRADE_SELF_UPDATE_RERUN_ACTIVE=0

log_upgrade_exit() {
  local status="${1:-$?}"
  arthexis_log_startup_event "$BASE_DIR" "$UPGRADE_SCRIPT_NAME" "finish" "status=$status"
  if declare -F arthexis_record_upgrade_duration >/dev/null 2>&1; then
    arthexis_record_upgrade_duration "$status"
  fi
}
trap log_upgrade_exit EXIT
# shellcheck source=scripts/helpers/ports.sh
. "$BASE_DIR/scripts/helpers/ports.sh"
# shellcheck source=scripts/helpers/version_marker.sh
. "$BASE_DIR/scripts/helpers/version_marker.sh"
# shellcheck source=scripts/helpers/auto-upgrade-service.sh
. "$BASE_DIR/scripts/helpers/auto-upgrade-service.sh"
# shellcheck source=scripts/helpers/post-upgrade-hooks.sh
. "$BASE_DIR/scripts/helpers/post-upgrade-hooks.sh"
# shellcheck source=scripts/helpers/systemd_locks.sh
. "$BASE_DIR/scripts/helpers/systemd_locks.sh"
# shellcheck source=scripts/helpers/service_manager.sh
. "$BASE_DIR/scripts/helpers/service_manager.sh"
# shellcheck source=scripts/helpers/suite-uptime-lock.sh
. "$BASE_DIR/scripts/helpers/suite-uptime-lock.sh"
# shellcheck source=scripts/helpers/timing.sh
. "$BASE_DIR/scripts/helpers/timing.sh"
arthexis_resolve_log_dir "$BASE_DIR" LOG_DIR || exit 1
# Prefer python3 but fall back to python when only the legacy binary is available.
DEFAULT_VENV_PYTHON="$BASE_DIR/.venv/bin/python"
if [ -x "$DEFAULT_VENV_PYTHON" ]; then
  PYTHON_BIN="$DEFAULT_VENV_PYTHON"
else
  PYTHON_BIN="$(command -v python3 || command -v python || true)"
fi
# Capture stdout/stderr to a timestamped log for later review.
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE")
exec 2> >(tee "$LOG_FILE" >&2)
cd "$BASE_DIR"

LOCK_DIR="$BASE_DIR/.locks"
ENV_REFRESH_PID_FILE="$LOCK_DIR/env-refresh.pid"
ENV_REFRESH_PID_DIR="$LOCK_DIR/env-refresh.pids"
SYSTEMD_UNITS_LOCK="$LOCK_DIR/systemd_services.lck"
SERVICE_NAME="${SERVICE_NAME:-}"
[[ -z "$SERVICE_NAME" ]] && [[ -f "$LOCK_DIR/service.lck" ]] && SERVICE_NAME="$(cat "$LOCK_DIR/service.lck")"

arthexis_ensure_upstream_remotes "$BASE_DIR"

arthexis_record_upgrade_duration() {
  local status="${1:-0}"
  local end_time
  end_time=$(date +%s)
  local duration=$((end_time - UPGRADE_STARTED_AT))
  local duration_python="${PYTHON_BIN:-}"
  if [ -z "$duration_python" ]; then
    duration_python="$(command -v python3 || command -v python || true)"
  fi
  if [ -z "$duration_python" ]; then
    echo "Warning: no Python interpreter available to record upgrade duration." >&2
    return 0
  fi
  if ! "$duration_python" - "$UPGRADE_DURATION_LOCK" "$UPGRADE_STARTED_AT" "$end_time" "$duration" "$status" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

lock_path = Path(sys.argv[1])
started_at = int(sys.argv[2])
finished_at = int(sys.argv[3])
duration = int(sys.argv[4])
status = int(sys.argv[5])

payload = {
    "started_at": datetime.fromtimestamp(started_at, tz=timezone.utc).isoformat(),
    "finished_at": datetime.fromtimestamp(finished_at, tz=timezone.utc).isoformat(),
    "duration_seconds": duration,
    "status": status,
}

lock_path.parent.mkdir(parents=True, exist_ok=True)
lock_path.write_text(json.dumps(payload), encoding="utf-8")
PY
  then
    echo "Warning: failed to record upgrade duration metadata." >&2
    return 0
  fi
}

auto_upgrade_enabled() {
  local base_dir="${1:-$BASE_DIR}"
  local lock_file="${base_dir}/.locks/auto_upgrade.lck"

  [ -f "$lock_file" ]
}

_discard_local_git_changes() {
  if ! git reset --hard HEAD >/dev/null 2>&1; then
    echo "Failed to discard local changes automatically; please commit or stash before upgrading." >&2
    exit 1
  fi

  if ! git clean -fd -e data/ >/dev/null 2>&1; then
    echo "Failed to remove untracked files automatically; please commit or stash before upgrading." >&2
    exit 1
  fi
}

mkdir -p "$LOCK_DIR"

ensure_git_safe_directory() {
  if ! command -v git >/dev/null 2>&1; then
    return 0
  fi

  # Avoid fatal "dubious ownership" errors when upgrades run under systemd users.
  if git config --global --get-all safe.directory "$BASE_DIR" >/dev/null 2>&1; then
    return 0
  fi

  git config --global --add safe.directory "$BASE_DIR" >/dev/null 2>&1 || true
}

print_pending_commit_messages() {
  local from_rev="$1"
  local to_rev="$2"

  if [[ -z "$from_rev" || -z "$to_rev" || "$from_rev" == "$to_rev" ]]; then
    return 0
  fi

  if ! git rev-parse --verify "${from_rev}^{commit}" >/dev/null 2>&1; then
    return 0
  fi

  if ! git rev-parse --verify "${to_rev}^{commit}" >/dev/null 2>&1; then
    return 0
  fi

  local pending_commits
  pending_commits=$(git log --pretty=format:'- %s' "${from_rev}..${to_rev}" 2>/dev/null || true)

  if [[ -n "$pending_commits" ]]; then
    echo "Pending updates include the following commits:"
    echo "$pending_commits"
  fi
}

collect_requirement_files() {
  local -n out_array="$1"

  mapfile -t out_array < <(find "$BASE_DIR" -maxdepth 1 -type f \
    \( -name 'requirements.txt' -o -name 'requirements-hw.txt' \) -print | sort)
}

compute_requirements_checksum() {
  local -a files=("$@")

  if [ ${#files[@]} -eq 0 ]; then
    echo ""
    return 0
  fi

  (
    for file in "${files[@]}"; do
      printf '%s\n' "${file##*/}"
      cat "$file"
    done
  ) | sha256sum | awk '{print $1}'
}

collect_requirement_files REQUIREMENT_FILES
REQ_HASH_FILE="$LOCK_DIR/requirements.bundle.sha256"
REQUIREMENTS_HASH=""
STORED_REQ_HASH=""
DEPENDENCY_REFRESH_REQUIRED=0

refresh_dependency_change_state() {
  collect_requirement_files REQUIREMENT_FILES
  REQUIREMENTS_HASH=""
  STORED_REQ_HASH=""
  if [ ${#REQUIREMENT_FILES[@]} -gt 0 ]; then
    REQUIREMENTS_HASH=$(compute_requirements_checksum "${REQUIREMENT_FILES[@]}")
    [ -f "$REQ_HASH_FILE" ] && STORED_REQ_HASH=$(cat "$REQ_HASH_FILE")
  fi
  DEPENDENCY_REFRESH_REQUIRED=0
  if [ -n "$REQUIREMENTS_HASH" ] && [ "$REQUIREMENTS_HASH" != "$STORED_REQ_HASH" ]; then
    DEPENDENCY_REFRESH_REQUIRED=1
  fi
  if [[ $FORCE_ENV_REFRESH -eq 1 ]]; then
    DEPENDENCY_REFRESH_REQUIRED=1
  fi
}

refresh_dependency_change_state

arthexis_timing_setup "upgrade"

# Lifecycle CLI contract: top-level upgrade flags and aliases are validated by tests to prevent accidental drift.

is_non_terminal_role() {
  case "$1" in
    Control|Constellation|Watchtower)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

pip_requires_break_system_packages() {
  local python_bin="$1"

  if [ -z "$python_bin" ] || ! command -v "$python_bin" >/dev/null 2>&1; then
    return 1
  fi

  # If we're already running inside a virtual environment, installing packages
  # won't touch system-managed locations, so we do not need the override.
  "$python_bin" - <<'PY'
import sys

if sys.prefix != sys.base_prefix:
    sys.exit(1)
PY

  "$python_bin" - <<'PY'
from pathlib import Path
import sys

version = f"python{sys.version_info.major}.{sys.version_info.minor}"
candidates = [
    Path(sys.base_prefix) / "EXTERNALLY-MANAGED",
    Path(sys.base_prefix) / "lib" / version / "EXTERNALLY-MANAGED",
]
sys.exit(0 if any(path.exists() for path in candidates) else 1)
PY
}

ensure_virtualenv() {
  local venv_dir="$BASE_DIR/.venv"
  local venv_python="$venv_dir/bin/python"
  local creator="$PYTHON_BIN"

  if [ -x "$venv_python" ]; then
    PYTHON_BIN="$venv_python"
    return 0
  fi

  if [ -z "$creator" ] || ! command -v "$creator" >/dev/null 2>&1; then
    creator="$(command -v python3 || command -v python || true)"
  fi

  if [ -z "$creator" ]; then
    echo "Python interpreter not found; cannot create virtual environment at $venv_dir." >&2
    return 1
  fi

  echo "Creating virtual environment at $venv_dir..."
  "$creator" -m venv "$venv_dir"

  if [ ! -x "$venv_python" ]; then
    echo "Failed to create virtual environment at $venv_dir." >&2
    return 1
  fi

  PYTHON_BIN="$venv_python"
  return 0
}

ensure_git_safe_directory

notify_lcd_manual_upgrade_required() {
  if ! arthexis_lcd_feature_enabled "$LOCK_DIR"; then
    return 0
  fi

  local lock_file="$LOCK_DIR/lcd_screen.lck"
  local state_line="state=enabled"
  if [ -f "$lock_file" ]; then
    local first_line
    first_line=$(head -n 1 "$lock_file" 2>/dev/null | tr -d '\r\n')
    if printf '%s' "$first_line" | grep -iq '^state='; then
      state_line="$first_line"
    fi
  fi

  printf "%s\nManual action:\n> upgrade.sh\n" "$state_line" > "$lock_file"
}

trigger_upgrade_reboot() {
  if command -v systemctl >/dev/null 2>&1; then
    if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
      sudo -n systemctl reboot || true
    else
      systemctl reboot || true
    fi
    return 0
  fi

  if command -v reboot >/dev/null 2>&1; then
    if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
      sudo -n reboot || true
    else
      reboot || true
    fi
    return 0
  fi

  echo "No reboot command available on this system." >&2
  return 1
}

remove_generated_cleanup_path() {
  local target="$1"
  local label="${2:-$target}"
  local attempt

  for attempt in 1 2 3; do
    if [ ! -e "$target" ]; then
      return 0
    fi

    if rm -rf -- "$target" 2>/dev/null; then
      return 0
    fi

    sleep 1
  done

  if [ -e "$target" ]; then
    echo "Warning: generated path $label could not be fully removed; continuing because runtime cache state must not block upgrades." >&2
  fi

  return 0
}

remove_generated_path_before_upgrade() {
  local generated_path="$1"

  if [ ! -e "$generated_path" ]; then
    return 0
  fi

  local cleanup_parent="$LOCK_DIR/generated-cleanup"
  local generated_name="${generated_path##*/}"
  local staged_path

  mkdir -p "$cleanup_parent" 2>/dev/null || true
  staged_path="$cleanup_parent/${generated_name}.$(date +%s).$$"
  while [ -e "$staged_path" ]; do
    staged_path="${staged_path}.${RANDOM:-0}"
  done

  echo "Removing generated path $generated_path before upgrading..."
  if mv -- "$generated_path" "$staged_path" 2>/dev/null; then
    remove_generated_cleanup_path "$staged_path" "$generated_path"
    return 0
  fi

  if [ ! -e "$generated_path" ]; then
    return 0
  fi

  echo "Warning: could not quarantine generated path $generated_path; attempting in-place cleanup." >&2
  remove_generated_cleanup_path "$generated_path" "$generated_path"
}

reset_safe_git_changes() {
  local role="${1:-Terminal}"

  # Discard known auto-generated files that should not block rebases.
  local safe_rebase_files=(
    "VERSION"
  )

  # Remove generated working directories that should stay untracked.
  local safe_generated_paths=(
    "cache"
  )

  # Restore tracked placeholders that may be removed by cleanup scripts.
  local safe_placeholder_files=(
    "logs/.gitkeep"
    "logs/old/.gitkeep"
  )

  if ! command -v git >/dev/null 2>&1; then
    return 0
  fi

  local generated_path
  for generated_path in "${safe_generated_paths[@]}"; do
    remove_generated_path_before_upgrade "$generated_path"
  done

  local placeholder
  for placeholder in "${safe_placeholder_files[@]}"; do
    if git ls-files --error-unmatch "$placeholder" >/dev/null 2>&1; then
      if [ ! -f "$placeholder" ] || ! git diff --quiet -- "$placeholder" 2>/dev/null; then
        echo "Restoring placeholder $placeholder before upgrading..."
        mkdir -p "$(dirname "$placeholder")"
        git checkout -- "$placeholder" 2>/dev/null || git restore "$placeholder" 2>/dev/null || true
      fi
    fi
  done

  local status_output
  if ! status_output=$(git status --porcelain 2>/dev/null); then
    return 0
  fi

  # Remove untracked merge migrations auto-generated by Django conflict
  # resolution so they do not block upgrades.
  local generated_merge_migrations=()
  while IFS= read -r status_line; do
    if [[ "${status_line:0:2}" == "?? " ]]; then
      local untracked_path="${status_line:3}"
      if [[ "$untracked_path" =~ ^apps/[^/]+/migrations/[0-9]+_merge_[0-9]{8}_[0-9]{4}\.py$ ]]; then
        generated_merge_migrations+=("$untracked_path")
      fi
    fi
  done <<< "$status_output"

  if [ ${#generated_merge_migrations[@]} -gt 0 ]; then
    local generated_merge_migration
    for generated_merge_migration in "${generated_merge_migrations[@]}"; do
      echo "Removing generated migration $generated_merge_migration before upgrading..."
      rm -f -- "$generated_merge_migration"
    done

    if ! status_output=$(git status --porcelain 2>/dev/null); then
      return 0
    fi
  fi

  local reset_candidates=()
  while IFS= read -r status_line; do
    [[ -z "$status_line" ]] && continue

    local path
    path="${status_line:3}"
    path="${path%% -> *}"

    for safe_file in "${safe_rebase_files[@]}"; do
      if [ "$path" = "$safe_file" ]; then
        reset_candidates+=("$path")
        break
      fi
    done
  done <<< "$status_output"

  if [ ${#reset_candidates[@]} -gt 0 ]; then
    echo "Discarding local changes for safe-to-replace files: ${reset_candidates[*]}"
    git checkout -- "${reset_candidates[@]}" 2>/dev/null || \
      git restore "${reset_candidates[@]}" 2>/dev/null || true
  fi

  if git status --porcelain 2>/dev/null | grep -q '^[ MADRCU?]'; then
    if is_non_terminal_role "$role"; then
      echo "Non-terminal role $role detected unstashed changes; discarding local modifications before upgrading..."
      _discard_local_git_changes
      return 0
    elif auto_upgrade_enabled "$BASE_DIR"; then
      echo "Auto-upgrade enabled; discarding local changes before upgrading..."
      _discard_local_git_changes
      return 0
    elif [[ ${FORCE_UPGRADE:-0} -eq 1 ]]; then
      echo "Force requested; discarding local working tree changes before upgrading..."
      _discard_local_git_changes
      return 0
    fi

    echo "Uncommitted changes detected before upgrading. Dirty paths:" >&2
    git status --short >&2 || true
    echo "Please commit or stash before upgrading." >&2
    exit 1
  fi
}

stash_local_changes_for_upgrade() {
  if ! command -v git >/dev/null 2>&1; then
    return 0
  fi

  if git status --porcelain 2>/dev/null | grep -q '^[ MADRCU?]'; then
    if ! git var GIT_COMMITTER_IDENT >/dev/null 2>&1; then
      if [[ $FORCE_STASH -ne 1 ]]; then
        echo "Skipping automatic stash because git committer identity is not configured. Pass --stash to force stashing." >&2
        return 0
      fi
    fi

    local stash_label
    stash_label="upgrade-$(date -u +%Y%m%dT%H%M%SZ)"

    if git stash push --include-untracked -m "$stash_label" >/dev/null 2>&1; then
      UPGRADE_STASH_REF="$(git stash list | head -n1 | cut -d: -f1)"
      UPGRADE_STASH_CREATED=1
      echo "Stashed local changes before upgrade as ${UPGRADE_STASH_REF:-latest stash}."
    else
      echo "Failed to stash local changes automatically; please commit or stash before upgrading." >&2
      exit 1
    fi
  fi
}

restore_stashed_changes_after_upgrade() {
  if [[ $UPGRADE_STASH_CREATED -ne 1 ]]; then
    return 0
  fi

  if [ -z "$UPGRADE_STASH_REF" ]; then
    return 0
  fi

  if git stash list | grep -q "^${UPGRADE_STASH_REF}:"; then
    if git stash pop "$UPGRADE_STASH_REF" >/dev/null 2>&1; then
      echo "Restored stashed local changes from $UPGRADE_STASH_REF after upgrade."
    else
      echo "Stashed changes remain in $UPGRADE_STASH_REF; apply them manually with 'git stash apply'." >&2
    fi
  fi
}

fetch_branch_with_ref_repair() {
  local remote="$1"
  local branch="$2"
  local fetch_output=""

  if fetch_output=$(arthexis_git_for_remote "$BASE_DIR" "$remote" fetch "$remote" "$branch" 2>&1); then
    if [ -n "$fetch_output" ]; then
      printf '%s\n' "$fetch_output"
    fi
    return 0
  fi

  if [ -n "$fetch_output" ]; then
    printf '%s\n' "$fetch_output" >&2
  fi

  if printf '%s\n' "$fetch_output" | grep -q "cannot lock ref 'refs/remotes/${remote}/${branch}'"; then
    echo "Detected stale remote-tracking ref for ${remote}/${branch}; pruning and retrying git fetch..." >&2
    git remote prune "$remote" >/dev/null 2>&1 || true
    git update-ref -d "refs/remotes/${remote}/${branch}" >/dev/null 2>&1 || true

    if fetch_output=$(arthexis_git_for_remote "$BASE_DIR" "$remote" fetch "$remote" "$branch" 2>&1); then
      if [ -n "$fetch_output" ]; then
        printf '%s\n' "$fetch_output"
      fi
      return 0
    fi

    if [ -n "$fetch_output" ]; then
      printf '%s\n' "$fetch_output" >&2
    fi
  fi

  return 1
}

resolve_pinned_release_target() {
  local resolved_revision=""
  local resolved_version=""
  local target_ref=""

  if [[ -n "$TARGET_TAG" ]]; then
    echo "Fetching pinned release tag $TARGET_TAG..."
    if ! arthexis_git_for_remote "$BASE_DIR" origin fetch origin "refs/tags/$TARGET_TAG:refs/tags/$TARGET_TAG"; then
      echo "Error: unable to fetch release tag $TARGET_TAG from origin." >&2
      exit 1
    fi
    target_ref="refs/tags/$TARGET_TAG"
  elif [[ -n "$TARGET_REVISION" ]]; then
    fetch_branch_with_ref_repair origin "$BRANCH" || true
    target_ref="$TARGET_REVISION"
  else
    echo "Pinned release upgrades require --target-tag or --target-revision." >&2
    exit 1
  fi

  if ! resolved_revision="$(git rev-parse "${target_ref}^{commit}" 2>/dev/null)"; then
    echo "Error: pinned release target $target_ref is not available locally." >&2
    exit 1
  fi

  if [[ -n "$TARGET_REVISION" && "$resolved_revision" != "$TARGET_REVISION" ]]; then
    echo "Error: pinned release target resolved to $resolved_revision, expected $TARGET_REVISION." >&2
    exit 1
  fi

  if ! git cat-file -e "$resolved_revision:VERSION" 2>/dev/null; then
    echo "Error: pinned release target $resolved_revision does not contain VERSION." >&2
    exit 1
  fi

  resolved_version="$(git show "$resolved_revision:VERSION" | tr -d '\r\n')"
  if [[ -n "$TARGET_VERSION" && "$resolved_version" != "$TARGET_VERSION" ]]; then
    echo "Error: pinned release target VERSION is $resolved_version, expected $TARGET_VERSION." >&2
    exit 1
  fi

  REMOTE_REVISION="$resolved_revision"
  REMOTE_VERSION="${TARGET_VERSION:-$resolved_version}"
}

broadcast_upgrade_start_net_message() {
  local local_revision="$1"
  local remote_revision="$2"

  if [ -z "$PYTHON_BIN" ]; then
    return 0
  fi

  "$PYTHON_BIN" - "$BASE_DIR" "$local_revision" "$remote_revision" <<'PY'
import os
import sys
from pathlib import Path

base_dir = Path(sys.argv[1])
local_rev = sys.argv[2] or None
remote_rev = sys.argv[3] or None

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
sys.path.insert(0, str(base_dir))

try:
    import django
    django.setup()
except Exception:
    sys.exit(0)

try:
    from apps.core.tasks.auto_upgrade import _broadcast_upgrade_start_message
except Exception:
    sys.exit(0)

try:
    _broadcast_upgrade_start_message(local_rev, remote_rev)
except Exception:
    sys.exit(1)
PY
}
SERVICE_MANAGEMENT_MODE="$(arthexis_detect_service_mode "$LOCK_DIR")"
UPGRADE_IN_PROGRESS_LOCK="$LOCK_DIR/upgrade_in_progress.lck"
# Discover managed service if not explicitly recorded.
if [ -z "$SERVICE_NAME" ]; then
  while IFS= read -r unit_name; do
    case "$unit_name" in
      *-upgrade-guard.service|*-upgrade-guard.timer|celery-*.service|celery-beat-*.service|lcd-*.service)
        continue
        ;;
    esac

    if [[ "$unit_name" == *.service ]]; then
      SERVICE_NAME="${unit_name%.service}"
      break
    fi
  done < <(arthexis_read_systemd_unit_records "$LOCK_DIR")
fi

if [ "$SERVICE_MANAGEMENT_MODE" = "$ARTHEXIS_SERVICE_MODE_EMBEDDED" ]; then
  if [ -n "$SERVICE_NAME" ]; then
    arthexis_remove_celery_unit_stack "$LOCK_DIR" "$SERVICE_NAME"
    arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "lcd-${SERVICE_NAME}.service"
  fi
  if [ -f "$SYSTEMD_UNITS_LOCK" ]; then
    while IFS= read -r recorded_unit; do
      case "$recorded_unit" in
        celery-*.service|celery-beat-*.service)
          arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "$recorded_unit"
          ;;
        lcd-*.service|camera-*.service)
          arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "$recorded_unit"
          ;;
      esac
    done < "$SYSTEMD_UNITS_LOCK"
  fi
fi

SYSTEMCTL_CMD=()
if command -v systemctl >/dev/null 2>&1; then
  SYSTEMCTL_CMD=(systemctl)
  if command -v sudo >/dev/null 2>&1; then
    if sudo -n true 2>/dev/null; then
      SYSTEMCTL_CMD=(sudo -n systemctl)
    else
      SYSTEMCTL_CMD=(systemctl)
    fi
  fi
fi

# Capture sudo/systemd locations for environments where the defaults are missing.
SUDO_CMD=(sudo)
if ! command -v sudo >/dev/null 2>&1; then
  SUDO_CMD=()
fi

SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"

retire_legacy_kiosk_units() {
  # Retired HDMI kiosk layout units ran privileged legacy scripts on older
  # installs. Always remove stale units during upgrade, even when minimal kiosk
  # features are reintroduced by current code.
  local legacy_unit
  local legacy_units_to_remove=()
  for legacy_unit in \
    "arthexis-hdmi-kiosk.service" \
    "arthexis-hdmi-kiosk-layout.service" \
    "arthexis-hdmi-kiosk-layout.path"
  do
    arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "$legacy_unit"
  done

  if [ -f "$SYSTEMD_UNITS_LOCK" ]; then
    while IFS= read -r legacy_unit; do
      legacy_unit="${legacy_unit%$'\r'}"
      case "$legacy_unit" in
        *-kiosk-layout-cleanup.service|*-kiosk-layout-cleanup.path)
          legacy_units_to_remove+=("$legacy_unit")
          ;;
      esac
    done < "$SYSTEMD_UNITS_LOCK"

    for legacy_unit in "${legacy_units_to_remove[@]}"; do
      arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "$legacy_unit"
    done
  fi
}

retire_workgroup_play_password_units() {
  # The Workgroup play password feature was removed. Stop and remove its
  # standalone timer on upgrades so older installations do not keep invoking
  # a deleted management command.
  arthexis_remove_systemd_unit_if_present \
    "$LOCK_DIR" "arthexis-workgroup-play-password.service"
  arthexis_remove_systemd_unit_if_present \
    "$LOCK_DIR" "arthexis-workgroup-play-password.timer"
}

lcd_service_lockfiles_present() {
  if [ ! -f "$SYSTEMD_UNITS_LOCK" ]; then
    return 1
  fi

  if grep -Eq '^lcd-.*\\.service$' "$SYSTEMD_UNITS_LOCK"; then
    return 0
  fi

  return 1
}

lcd_service_configured() {
  arthexis_lcd_service_configured "$LOCK_DIR" "$1" "$SERVICE_MANAGEMENT_MODE"
}

lcd_systemd_unit_present() {
  _prefixed_systemd_unit_present "lcd" "$1"
}

celery_systemd_unit_present() {
  _prefixed_systemd_unit_present "celery" "$1"
}

celery_beat_systemd_unit_present() {
  _prefixed_systemd_unit_present "celery-beat" "$1"
}

rfid_service_configured() {
  [ -f "$LOCK_DIR/$ARTHEXIS_RFID_SERVICE_LOCK" ]
}

rfid_systemd_unit_present() {
  _prefixed_systemd_unit_present "rfid" "$1"
}

camera_service_configured() {
  [ -f "$LOCK_DIR/$ARTHEXIS_CAMERA_SERVICE_LOCK" ]
}

camera_systemd_unit_present() {
  _prefixed_systemd_unit_present "camera" "$1"
}

_prefixed_systemd_unit_present() {
  local prefix="$1"
  local service_name="$2"

  if [ -z "$prefix" ] || [ -z "$service_name" ] || [ "$SERVICE_MANAGEMENT_MODE" != "$ARTHEXIS_SERVICE_MODE_SYSTEMD" ]; then
    return 1
  fi

  local unit_name
  unit_name="${prefix}-${service_name}.service"

  if [ -f "${SYSTEMD_DIR}/${unit_name}" ]; then
    return 0
  fi

  if command -v systemctl >/dev/null 2>&1; then
    systemctl list-unit-files | awk '{print $1}' | grep -Fxq "$unit_name"
    return $?
  fi

  return 1
}

# Repair any auto-upgrade working directory to keep services consistent before modifying systemd.
if [ -n "$SERVICE_NAME" ]; then
  arthexis_repair_auto_upgrade_workdir "$BASE_DIR" "$SERVICE_NAME" "$SYSTEMD_DIR"
fi

start_lcd_upgrade_helper_service() {
  if [ ${#SYSTEMCTL_CMD[@]} -eq 0 ] || { [ ${#SUDO_CMD[@]} -eq 0 ] && [ ! -w "$SYSTEMD_DIR" ]; }; then
    return 0
  fi

  if ! lcd_service_lockfiles_present; then
    return 0
  fi

  local helper_script
  helper_script="$BASE_DIR/scripts/helpers/lcd-upgrade-helper.py"
  if [ ! -f "$helper_script" ]; then
    return 0
  fi

  local helper_service
  helper_service="lcd-upgrade-helper.service"
  local helper_service_file
  helper_service_file="${SYSTEMD_DIR}/${helper_service}"

  local helper_user
  helper_user="$(arthexis_detect_service_user "$BASE_DIR")"

  local helper_python
  helper_python="$PYTHON_BIN"
  if [ -z "$helper_python" ] || [ ! -x "$helper_python" ]; then
    helper_python="$(command -v python3 || command -v python || true)"
  fi

  if [ -z "$helper_python" ]; then
    return 0
  fi

  if [ ${#SUDO_CMD[@]} -gt 0 ]; then
    "${SUDO_CMD[@]}" bash -c "cat > '$helper_service_file' <<SERVICEEOF
[Unit]
Description=LCD helper reminder to rerun upgrade.sh after script update
After=network-online.target

[Service]
Type=simple
WorkingDirectory=${BASE_DIR}
ExecStart=${helper_python} ${helper_script}
User=${helper_user}
Restart=no

[Install]
WantedBy=multi-user.target
SERVICEEOF"
  else
    bash -c "cat > '$helper_service_file' <<SERVICEEOF
[Unit]
Description=LCD helper reminder to rerun upgrade.sh after script update
After=network-online.target

[Service]
Type=simple
WorkingDirectory=${BASE_DIR}
ExecStart=${helper_python} ${helper_script}
User=${helper_user}
Restart=no

[Install]
WantedBy=multi-user.target
SERVICEEOF"
  fi

  if [ ${#SYSTEMCTL_CMD[@]} -gt 0 ]; then
    "${SYSTEMCTL_CMD[@]}" daemon-reload >/dev/null 2>&1 || true
    "${SYSTEMCTL_CMD[@]}" start "$helper_service" >/dev/null 2>&1 || true
  fi
}

# Remove deprecated systemd prestart environment refresh hooks before starting services.
remove_prestart_env_refresh() {
  local service="$1"

  if [ -z "$service" ]; then
    return 0
  fi

  local service_file="${SYSTEMD_DIR}/${service}.service"
  local refresh_pattern="^ExecStartPre=.*/scripts/prestart-refresh\\.sh$"

  if [ ! -f "$service_file" ]; then
    return 0
  fi

  if grep -Eq "$refresh_pattern" "$service_file"; then
    if [ ${#SUDO_CMD[@]} -gt 0 ]; then
      "${SUDO_CMD[@]}" sed -i "\~${refresh_pattern}~d" "$service_file"
    else
      sed -i "\~${refresh_pattern}~d" "$service_file"
    fi

    if [ ${#SYSTEMCTL_CMD[@]} -gt 0 ]; then
      "${SYSTEMCTL_CMD[@]}" daemon-reload >/dev/null 2>&1 || true
    fi

    echo "Removed deprecated prestart environment refresh from ${service}.service."
  fi
}

determine_node_role() {
  if [ -n "${NODE_ROLE:-}" ]; then
    echo "$NODE_ROLE"
    return
  fi

  local role_file="$LOCK_DIR/role.lck"
  if [ -f "$role_file" ]; then
    local role
    role=$(tr -d '\r\n' < "$role_file")
    if [ -n "$role" ]; then
      echo "$role"
      return
    fi
  fi

  echo "Terminal"
}

env_refresh_process_start_time() {
  local pid="$1"
  if [ -r "/proc/$pid/stat" ]; then
    awk 'sub(/^.*\)/, "") {print $20}' "/proc/$pid/stat" 2>/dev/null || true
  fi
}

clear_stale_env_refresh_pid_file() {
  local pid_file="${1:-$ENV_REFRESH_PID_FILE}"
  rm -f "$pid_file" 2>/dev/null || true
  rmdir "$ENV_REFRESH_PID_DIR" 2>/dev/null || true
}

env_refresh_physical_dir() {
  local path="$1"
  if [ -d "$path" ]; then
    (cd "$path" && pwd -P)
  fi
}

env_refresh_pid_file_active() {
  local pid_file="$1"
  if [ ! -f "$pid_file" ]; then
    return 1
  fi

  local base_dir=""
  local expected_base_dir
  local key
  local physical_base_dir=""
  local pid=""
  local pid_file_owner=""
  local proc_owner=""
  local start_time=""
  local value
  while IFS='=' read -r key value; do
    case "$key" in
      base_dir) base_dir="$value" ;;
      physical_base_dir) physical_base_dir="$value" ;;
      pid) pid="$value" ;;
      start_time) start_time="$value" ;;
    esac
  done < "$pid_file"

  expected_base_dir="$(env_refresh_physical_dir "$BASE_DIR")"
  if [ -z "$physical_base_dir" ] && [ -n "$base_dir" ]; then
    physical_base_dir="$(env_refresh_physical_dir "$base_dir")"
  fi

  if [[ ! "$pid" =~ ^[0-9]+$ ]] || [ -z "$expected_base_dir" ] || [ -z "$physical_base_dir" ] || [ "$physical_base_dir" != "$expected_base_dir" ]; then
    clear_stale_env_refresh_pid_file "$pid_file"
    return 1
  fi

  if ! kill -0 "$pid" >/dev/null 2>&1; then
    clear_stale_env_refresh_pid_file "$pid_file"
    return 1
  fi

  if command -v stat >/dev/null 2>&1 && [ -e "/proc/$pid" ]; then
    pid_file_owner="$(stat -c %u "$pid_file" 2>/dev/null || true)"
    proc_owner="$(stat -c %u "/proc/$pid" 2>/dev/null || true)"
    if [ -n "$pid_file_owner" ] && [ -n "$proc_owner" ] && [ "$pid_file_owner" != "$proc_owner" ]; then
      clear_stale_env_refresh_pid_file "$pid_file"
      return 1
    fi
  fi

  if [ -n "$start_time" ]; then
    local actual_start_time
    actual_start_time="$(env_refresh_process_start_time "$pid")"
    if [ -n "$actual_start_time" ] && [ "$actual_start_time" != "$start_time" ]; then
      clear_stale_env_refresh_pid_file "$pid_file"
      return 1
    fi
  fi

  if [ -e "/proc/$pid/cwd" ]; then
    local actual_cwd
    actual_cwd="$(readlink "/proc/$pid/cwd" 2>/dev/null || true)"
    if [ -n "$actual_cwd" ] && [ "$actual_cwd" != "$expected_base_dir" ]; then
      clear_stale_env_refresh_pid_file "$pid_file"
      return 1
    fi
  fi

  return 0
}

env_refresh_in_progress() {
  local pid_file
  if [ -d "$ENV_REFRESH_PID_DIR" ]; then
    for pid_file in "$ENV_REFRESH_PID_DIR"/*.pid; do
      [ -e "$pid_file" ] || continue
      if env_refresh_pid_file_active "$pid_file"; then
        return 0
      fi
    done
  fi

  if env_refresh_pid_file_active "$ENV_REFRESH_PID_FILE"; then
    return 0
  fi

  return 1
}

wait_for_env_refresh_idle() {
  local timeout_seconds="${1:-300}"
  local start_time
  start_time=$(date +%s)

  while env_refresh_in_progress; do
    if [ $(( $(date +%s) - start_time )) -ge "$timeout_seconds" ]; then
      echo "Timed out waiting for env-refresh to finish before restarting services." >&2
      return 1
    fi
    echo "Environment refresh is still running; waiting before service restart..."
    sleep 2
  done

  return 0
}

cleanup_non_terminal_git_state() {
  local role="$1"

  if ! is_non_terminal_role "$role"; then
    return
  fi

  if [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; then
    echo "Detected interrupted rebase; aborting before continuing upgrade..."
    git rebase --abort >/dev/null 2>&1 || true
  fi

  if [ -f .git/MERGE_HEAD ]; then
    echo "Detected interrupted merge; aborting before continuing upgrade..."
    git merge --abort >/dev/null 2>&1 || git reset --merge >/dev/null 2>&1 || true
  fi

  if [ -f .git/CHERRY_PICK_HEAD ]; then
    echo "Detected interrupted cherry-pick; aborting before continuing upgrade..."
    git cherry-pick --abort >/dev/null 2>&1 || true
  fi
}

install_requirements_if_changed() {
  local req_file="$BASE_DIR/requirements.txt"
  local hash_file="$LOCK_DIR/requirements.sha256"
  local venv_python="$BASE_DIR/.venv/bin/python"
  local new_hash=""
  local stored_hash=""

  if [ ! -f "$req_file" ]; then
    echo "requirements.txt not found; skipping dependency sync."
    return
  fi

  if ! ensure_virtualenv; then
    echo "Virtual environment Python not found; run ./install.sh before upgrading dependencies." >&2
    return 1
  fi

  local python_bin="$venv_python"

  new_hash=$(sha256sum "$req_file" | awk '{print $1}')
  if [ -f "$hash_file" ]; then
    stored_hash=$(cat "$hash_file")
  fi

  if [ "$new_hash" != "$stored_hash" ]; then
    if [ -f "$PIP_INSTALL_HELPER" ]; then
      "$python_bin" "$PIP_INSTALL_HELPER" -r "$req_file"
    else
      "$python_bin" -m pip install -r "$req_file"
    fi
    echo "$new_hash" > "$hash_file"
  else
    echo "Requirements unchanged. Skipping installation."
  fi
}

auto_realign_branch_for_role() {
  local role="$1"
  local branch="$2"

  if ! is_non_terminal_role "$role"; then
    return
  fi

  local behind=0 ahead=0
  if read -r behind ahead < <(git rev-list --left-right --count "origin/$branch...HEAD" 2>/dev/null); then
    :
  else
    behind=0
    ahead=0
  fi

  local dirty=0
  if ! git diff --quiet || ! git diff --cached --quiet; then
    dirty=1
  fi

  local has_untracked=0
  if [ -n "$(git ls-files --others --exclude-standard)" ]; then
    has_untracked=1
  fi

  if (( ahead > 0 )); then
    echo "Node role $role does not keep local commits; discarding $ahead local commit(s) to match origin/$branch..."
    git reset --hard "origin/$branch"
  elif (( dirty )); then
    echo "Discarding local working tree changes for $role node before pulling updates..."
    git reset --hard
  fi

  if (( has_untracked == 1 )); then
    echo "Removing untracked files for $role node before pulling updates (preserving data/)..."
    git clean -fd -e data/
  fi
}

CHANNEL="stable"
FORCE_STOP=0
STOP_CONFIRM=0
FORCE_UPGRADE=0
FORCE_ENV_REFRESH=0
CLEAN=0
NO_RESTART=0
STOP_ONLY=0
FORCE_START=0
FORCE_STASH=0
NO_WARN=0
LOCAL_ONLY=0
DETACHED=0
CHECK_ONLY=0
PRE_CHECK=0
REVERT_UPGRADE=0
CLEAR_LOGS=0
CLEAR_WORK=0
MIGRATE_RECONCILE=0
AUTO_RECONCILE_ON_MISMATCH=0
REQUESTED_BRANCH=""
REVERT_TARGET_REVISION=""
TARGET_VERSION=""
TARGET_REVISION=""
TARGET_TAG=""
FORWARDED_ARGS=()
# Parse CLI options controlling the upgrade strategy.
while [[ $# -gt 0 ]]; do
  case "$1" in
    --latest|--unstable|-l|-t)
      CHANNEL="unstable"
      FORWARDED_ARGS+=("--latest")
      shift
      ;;
    --force|-f)
      FORCE_STOP=1
      FORCE_UPGRADE=1
      FORWARDED_ARGS+=("--force")
      shift
      ;;
    --confirm)
      STOP_CONFIRM=1
      FORWARDED_ARGS+=("$1")
      shift
      ;;
    --stash)
      FORCE_STASH=1
      FORWARDED_ARGS+=("$1")
      shift
      ;;
    --force-refresh)
      FORCE_ENV_REFRESH=1
      FORWARDED_ARGS+=("$1")
      shift
      ;;
    --clean)
      CLEAN=1
      FORWARDED_ARGS+=("$1")
      shift
      ;;
    --migrate)
      MIGRATE_RECONCILE=1
      FORWARDED_ARGS+=("$1")
      shift
      ;;
    --reconcile)
      AUTO_RECONCILE_ON_MISMATCH=1
      FORWARDED_ARGS+=("$1")
      shift
      ;;
    --no-start|--no-restart)
      NO_RESTART=1
      FORCE_START=0
      FORWARDED_ARGS+=("$1")
      shift
      ;;
    --stop)
      STOP_ONLY=1
      NO_RESTART=1
      FORCE_START=0
      FORWARDED_ARGS+=("$1")
      shift
      ;;
    --start|-s)
      FORCE_START=1
      NO_RESTART=0
      FORWARDED_ARGS+=("--start")
      shift
      ;;
    --no-warn)
      NO_WARN=1
      FORWARDED_ARGS+=("$1")
      shift
      ;;
    --pre-check)
      PRE_CHECK=1
      FORWARDED_ARGS+=("$1")
      shift
      ;;
    --no-check)
      PRE_CHECK=0
      FORWARDED_ARGS+=("$1")
      shift
      ;;
    --clear-logs)
      CLEAR_LOGS=1
      FORWARDED_ARGS+=("$1")
      shift
      ;;
    --clear-work)
      CLEAR_WORK=1
      FORWARDED_ARGS+=("$1")
      shift
      ;;
    --local)
      LOCAL_ONLY=1
      FORWARDED_ARGS+=("$1")
      shift
      ;;
    --detached)
      DETACHED=1
      shift
      ;;
    --check)
      CHECK_ONLY=1
      shift
      ;;
    --revert)
      REVERT_UPGRADE=1
      FORWARDED_ARGS+=("$1")
      shift
      ;;
    --branch)
      if [[ -z "${2:-}" ]]; then
        echo "--branch requires an argument" >&2
        exit 1
      fi

      REQUESTED_BRANCH="$2"
      FORWARDED_ARGS+=("$1" "$2")
      shift 2
      ;;
    --target-version)
      if [[ -z "${2:-}" ]]; then
        echo "--target-version requires an argument" >&2
        exit 1
      fi

      TARGET_VERSION="$2"
      FORWARDED_ARGS+=("$1" "$2")
      shift 2
      ;;
    --target-revision)
      if [[ -z "${2:-}" ]]; then
        echo "--target-revision requires an argument" >&2
        exit 1
      fi

      TARGET_REVISION="$2"
      FORWARDED_ARGS+=("$1" "$2")
      shift 2
      ;;
    --target-tag)
      if [[ -z "${2:-}" ]]; then
        echo "--target-tag requires an argument" >&2
        exit 1
      fi

      TARGET_TAG="$2"
      FORWARDED_ARGS+=("$1" "$2")
      shift 2
      ;;
    --main)
      REQUESTED_BRANCH="main"
      FORWARDED_ARGS+=("$1")
      shift
      ;;
    --stable|--lts)
      CHANNEL="stable"
      FORWARDED_ARGS+=("$1")
      shift
      ;;
    --normal|--regular)
      CHANNEL="regular"
      FORWARDED_ARGS+=("$1")
      shift
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

target_pin_requested() {
  [[ -n "$TARGET_VERSION" || -n "$TARGET_REVISION" || -n "$TARGET_TAG" ]]
}

if [[ "$CHANNEL" == "unstable" ]] && target_pin_requested; then
  echo "Pinned release targets cannot be combined with --latest/--unstable." >&2
  exit 1
fi

if [[ $CLEAN -eq 1 && ( $MIGRATE_RECONCILE -eq 1 || $AUTO_RECONCILE_ON_MISMATCH -eq 1 ) ]]; then
  echo "Cannot combine --clean with --migrate or --reconcile." >&2
  echo "Use --migrate or --reconcile on their own to preserve and reconcile the pre-upgrade database (SQLite or PostgreSQL)." >&2
  exit 1
fi

rerun_with_updated_script() {
  local depth="${ARTHEXIS_UPGRADE_SELF_UPDATE_DEPTH:-0}"

  if [[ ! "$depth" =~ ^[0-9]+$ ]]; then
    depth=0
  fi

  if (( depth >= 1 )); then
    echo "upgrade.sh was updated again during self-update rerun; run ./upgrade.sh manually to continue." >&2
    return 1
  fi

  local -a rerun_cmd=("$UPGRADE_SCRIPT_PATH")
  if [ ${#FORWARDED_ARGS[@]} -gt 0 ]; then
    rerun_cmd+=("${FORWARDED_ARGS[@]}")
  fi

  echo "upgrade.sh was updated during git pull; restarting upgrade automatically with the new script..."
  UPGRADE_SELF_UPDATE_RERUN_ACTIVE=1
  export ARTHEXIS_UPGRADE_SELF_UPDATE_DEPTH=$((depth + 1))
  "${rerun_cmd[@]}"
}

run_detached_upgrade() {
  local delegated_script="$BASE_DIR/scripts/delegated-upgrade.sh"

  if [ ! -x "$delegated_script" ]; then
    echo "Detached upgrades require $delegated_script" >&2
    exit 1
  fi

  local upgrade_cmd=("$UPGRADE_SCRIPT_PATH")
  if [ ${#FORWARDED_ARGS[@]} -gt 0 ]; then
    upgrade_cmd+=("${FORWARDED_ARGS[@]}")
  fi

  echo "Launching detached upgrade via $delegated_script..."
  "$delegated_script" "${upgrade_cmd[@]}"
  exit $?
}

if (( DETACHED )); then
  run_detached_upgrade
fi

# Prime sudo credentials on interactive WSL sessions only when upgrade actions
# may require privileged systemd operations (skip read-only --check mode).
if [[ $CHECK_ONLY -ne 1 ]]; then
  arthexis_prime_sudo_credentials >/dev/null 2>&1 || true
fi

mkdir -p "$LOCK_DIR"

if [[ $CHECK_ONLY -ne 1 ]]; then
  retire_legacy_kiosk_units
  retire_workgroup_play_password_units
fi

# Mark upgrade progress so status.sh can surface active runs.
printf "%s\n" "$(date -Iseconds)" > "$UPGRADE_IN_PROGRESS_LOCK"
cleanup_upgrade_progress_lock() {
  rm -f "$UPGRADE_IN_PROGRESS_LOCK"
}

finalize_upgrade_exit() {
  local status=$?

  cleanup_upgrade_progress_lock
  log_upgrade_exit "$status"

  return "$status"
}

trap finalize_upgrade_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

UPGRADE_RERUN_LOCK="$LOCK_DIR/upgrade_rerun_required.lck"
RERUN_AFTER_SELF_UPDATE=0
RERUN_TARGET_VERSION=""
RERUN_INITIAL_VERSION=""
RERUN_INITIAL_REVISION=""
RERUN_SERVICE_WAS_ACTIVE=0
RERUN_LCD_WAS_ACTIVE=0
if [ -f "$UPGRADE_RERUN_LOCK" ]; then
  RERUN_AFTER_SELF_UPDATE=1
  while IFS= read -r rerun_line; do
    case "$rerun_line" in
      REMOTE_VERSION=*)
        RERUN_TARGET_VERSION="${rerun_line#REMOTE_VERSION=}"
        ;;
      LOCAL_VERSION=*)
        RERUN_INITIAL_VERSION="${rerun_line#LOCAL_VERSION=}"
        ;;
      LOCAL_REVISION=*)
        RERUN_INITIAL_REVISION="${rerun_line#LOCAL_REVISION=}"
        ;;
      SERVICE_WAS_ACTIVE=*)
        RERUN_SERVICE_WAS_ACTIVE="${rerun_line#SERVICE_WAS_ACTIVE=}"
        ;;
      LCD_WAS_ACTIVE=*)
        RERUN_LCD_WAS_ACTIVE="${rerun_line#LCD_WAS_ACTIVE=}"
        ;;
      UPGRADE_STASH_REF=*)
        UPGRADE_STASH_REF="${rerun_line#UPGRADE_STASH_REF=}"
        ;;
      UPGRADE_STASH_CREATED=*)
        UPGRADE_STASH_CREATED="${rerun_line#UPGRADE_STASH_CREATED=}"
        ;;
      *)
        if [ -z "$RERUN_TARGET_VERSION" ]; then
          RERUN_TARGET_VERSION="$(printf '%s' "$rerun_line" | tr -d '\r\n')"
        fi
        ;;
    esac
  done < "$UPGRADE_RERUN_LOCK"
  rm -f "$UPGRADE_RERUN_LOCK"
fi

# Wait for systemd services to report healthy before proceeding.
print_service_diagnostics() {
  local service="$1"
  shift
  local -a systemctl_cmd=("$@")

  if [ -z "$service" ] || ! command -v systemctl >/dev/null 2>&1; then
    return
  fi

  local -a journalctl_cmd=(journalctl)
  if command -v sudo >/dev/null 2>&1; then
    if sudo -n true 2>/dev/null; then
      journalctl_cmd=(sudo -n journalctl)
    fi
  fi

  echo "Diagnostics for $service:"
  "${systemctl_cmd[@]}" status "$service" --no-pager || true
  echo "Recent logs for $service:" >&2
  "${journalctl_cmd[@]}" -u "$service" -n 50 --no-pager || true
  echo "For more details, run:" >&2
  echo "  ${systemctl_cmd[*]} status $service" >&2
  echo "  ${journalctl_cmd[*]} -u $service -n 200 --since \"1 hour ago\"" >&2
}

wait_for_service_active() {
  local service="$1"
  local require_registered="${2:-0}"
  if [ -z "$service" ]; then
    return 0
  fi
  if ! command -v systemctl >/dev/null 2>&1; then
    return 0
  fi

  local -a systemctl_cmd=(systemctl)
  if command -v sudo >/dev/null 2>&1; then
    if sudo -n true 2>/dev/null; then
      systemctl_cmd=(sudo -n systemctl)
    else
      systemctl_cmd=(systemctl)
    fi
  fi

  if ! "${systemctl_cmd[@]}" list-unit-files | grep -Fq "${service}.service"; then
    if [ "$require_registered" -eq 1 ]; then
      echo "Service $service is not registered with systemd." >&2
      return 1
    fi
    return 0
  fi

  local timeout="${ARTHEXIS_WAIT_FOR_ACTIVE_TIMEOUT:-60}"
  if [[ ! "$timeout" =~ ^[0-9]+$ ]] || [ "$timeout" -le 0 ]; then
    timeout=60
  fi
  local deadline=$((SECONDS + timeout))
  echo "Waiting for service $service to report active..."
  while (( SECONDS < deadline )); do
    local status
    status=$("${systemctl_cmd[@]}" is-active "$service" 2>/dev/null || true)
    case "$status" in
      active)
        echo "Service $service is active."
        if [ "$service" = "$SERVICE_NAME" ]; then
          arthexis_service_access_message "$BASE_DIR"
        fi
        return 0
        ;;
      failed)
        echo "Service $service reported failed status." >&2
        print_service_diagnostics "$service" "${systemctl_cmd[@]}"
        return 1
        ;;
    esac
    sleep 2
  done

  echo "Timed out waiting for service $service to become active." >&2
  print_service_diagnostics "$service" "${systemctl_cmd[@]}"
  return 1
}

service_was_active() {
  local service_name="$1"

  if [ -z "$service_name" ]; then
    return 2
  fi

  local unit_known=0
  if [ ${#SYSTEMCTL_CMD[@]} -gt 0 ] && \
     "${SYSTEMCTL_CMD[@]}" list-unit-files | awk '{print $1}' | grep -Fxq "${service_name}.service"; then
    unit_known=1
    if "${SYSTEMCTL_CMD[@]}" is-active --quiet "$service_name"; then
      return 0
    fi
    return 1
  fi

  if pgrep -f "manage.py runserver" >/dev/null 2>&1; then
    return 0
  fi

  if [ "$unit_known" -eq 0 ]; then
    return 2
  fi

  return 1
}

lcd_service_was_active() {
  local service_name="$1"

  if [ -z "$service_name" ]; then
    return 1
  fi

  if [ ${#SYSTEMCTL_CMD[@]} -gt 0 ] && \
     "${SYSTEMCTL_CMD[@]}" list-unit-files | awk '{print $1}' | grep -Fxq "lcd-${service_name}.service"; then
    if "${SYSTEMCTL_CMD[@]}" is-active --quiet "lcd-${service_name}"; then
      return 0
    fi
    return 1
  fi

  return 1

  return 1
}

# Restart core, LCD, and Celery services while respecting systemd when available.
_ensure_service_active() {
  local service_unit="$1"
  local service_desc="$2"
  local systemctl_available="$3"
  shift 3
  local -a systemctl_cmd=("$@")

  if ! wait_for_service_active "$service_unit" 1; then
    if [ "$systemctl_available" -eq 1 ]; then
      echo "$service_desc service $service_unit did not become active after restart. Attempting manual start..." >&2
      if "${systemctl_cmd[@]}" start "$service_unit"; then
        if ! wait_for_service_active "$service_unit" 1; then
          echo "$service_desc service $service_unit did not become active after manual start." >&2
          return 1
        fi
      else
        echo "$service_desc service $service_unit failed to start manually." >&2
        return 1
      fi
    else
      echo "$service_desc service $service_unit did not become active after restart, and systemctl is unavailable for manual start." >&2
      return 1
    fi
  fi

  return 0
}

_check_service_active() {
  local service_unit="$1"
  local service_desc="$2"

  if ! wait_for_service_active "$service_unit" 1; then
    echo "$service_desc service $service_unit did not become active after restart." >&2
    return 1
  fi

  return 0
}

restart_services() {
  local include_lcd="${1:-1}"
  echo "Restarting services..."
  local service_name="${SERVICE_NAME:-}"
  if [[ -z "$service_name" ]] && [[ -f "$LOCK_DIR/service.lck" ]]; then
    service_name="$(cat "$LOCK_DIR/service.lck")"
  fi
  if [ -n "$service_name" ]; then
    if [ "$include_lcd" -eq 1 ]; then
      arthexis_disable_lcd_modes "$LOCK_DIR" "$service_name"
    fi
    local env_refresh_running=0
    if env_refresh_in_progress; then
      env_refresh_running=1
    fi
    if [ "$env_refresh_running" -eq 1 ]; then
      if ! wait_for_env_refresh_idle 300; then
        return 1
      fi
    fi
    local include_rfid=0
    if rfid_service_configured && rfid_systemd_unit_present "$service_name"; then
      include_rfid=1
    fi
    local include_camera=0
    if camera_service_configured && camera_systemd_unit_present "$service_name"; then
      include_camera=1
    fi
    local restart_via_systemd=0
    local systemctl_available=0
    local -a systemctl_cmd=()
    if command -v systemctl >/dev/null 2>&1; then
      systemctl_available=1
      systemctl_cmd=(systemctl)
      if command -v sudo >/dev/null 2>&1; then
        if sudo -n true 2>/dev/null; then
          systemctl_cmd=(sudo -n systemctl)
        else
          systemctl_cmd=(systemctl)
        fi
      fi
      echo "Existing services before restart:"
      "${systemctl_cmd[@]}" status "$service_name" --no-pager || true
      if "${systemctl_cmd[@]}" is-active --quiet "$service_name"; then
        echo "Signaling $service_name to restart via systemd..."
        "${systemctl_cmd[@]}" kill --signal=TERM "$service_name" || true
        restart_via_systemd=1
      fi
      if [ "$include_lcd" -eq 1 ] && lcd_systemd_unit_present "$service_name"; then
        local lcd_service="lcd-$service_name"
        if "${systemctl_cmd[@]}" is-active --quiet "$lcd_service"; then
          echo "Signaling $lcd_service for restart via systemd..."
          "${systemctl_cmd[@]}" kill --signal=TERM "$lcd_service" || true
        fi
      fi
      if [ "$include_rfid" -eq 1 ]; then
        local rfid_service="rfid-$service_name"
        if "${systemctl_cmd[@]}" is-active --quiet "$rfid_service"; then
          echo "Signaling $rfid_service for restart via systemd..."
          "${systemctl_cmd[@]}" kill --signal=TERM "$rfid_service" || true
        else
          echo "Starting $rfid_service via systemd..."
          "${systemctl_cmd[@]}" start "$rfid_service" || true
        fi
      fi
      if [ "$include_camera" -eq 1 ]; then
        local camera_service="camera-$service_name"
        if "${systemctl_cmd[@]}" is-active --quiet "$camera_service"; then
          echo "Signaling $camera_service for restart via systemd..."
          "${systemctl_cmd[@]}" kill --signal=TERM "$camera_service" || true
        else
          echo "Starting $camera_service via systemd..."
          "${systemctl_cmd[@]}" start "$camera_service" || true
        fi
      fi
    fi
    if [ "$restart_via_systemd" -eq 1 ]; then
      if ! wait_for_service_active "$service_name" 1; then
        echo "Service $service_name did not become active after restart." >&2
        return 1
      fi
      if [ "$include_lcd" -eq 1 ] && lcd_systemd_unit_present "$service_name"; then
        local lcd_service="lcd-$service_name"
        _ensure_service_active "$lcd_service" "LCD" "$systemctl_available" "${systemctl_cmd[@]}" || return 1
        LCD_RESTART_COMPLETED=1
      elif [ "$include_lcd" -eq 0 ] && lcd_systemd_unit_present "$service_name"; then
        local lcd_service="lcd-$service_name"
        if wait_for_service_active "$lcd_service" 1; then
          LCD_RESTART_COMPLETED=1
        fi
      fi
      if [ -f "$LOCK_DIR/celery.lck" ] && [ "$SERVICE_MANAGEMENT_MODE" = "$ARTHEXIS_SERVICE_MODE_SYSTEMD" ]; then
        local celery_service="celery-$service_name"
        local celery_unit="${celery_service}.service"
        local celery_beat_service="celery-beat-$service_name"
        local celery_beat_unit="${celery_beat_service}.service"

        if arthexis_systemd_unit_recorded "$LOCK_DIR" "$celery_unit" && \
           celery_systemd_unit_present "$service_name"; then
          _ensure_service_active "$celery_service" "Celery" "$systemctl_available" "${systemctl_cmd[@]}" || return 1
        else
          echo "Skipping $celery_unit; it is not both recorded and registered with systemd."
        fi
        if arthexis_systemd_unit_recorded "$LOCK_DIR" "$celery_beat_unit" && \
           celery_beat_systemd_unit_present "$service_name"; then
          _ensure_service_active "$celery_beat_service" "Celery beat" "$systemctl_available" "${systemctl_cmd[@]}" || return 1
        else
          echo "Skipping $celery_beat_unit; it is not both recorded and registered with systemd."
        fi
      fi
      if [ "$include_rfid" -eq 1 ]; then
        local rfid_service="rfid-$service_name"
        _ensure_service_active "$rfid_service" "RFID" "$systemctl_available" "${systemctl_cmd[@]}" || return 1
      fi
      if [ "$include_camera" -eq 1 ]; then
        local camera_service="camera-$service_name"
        _ensure_service_active "$camera_service" "Camera" "$systemctl_available" "${systemctl_cmd[@]}" || return 1
      fi
      return 0
    fi
    if ! ./start.sh; then
      echo "Service restart command failed." >&2
      return 1
    fi
    if ! wait_for_service_active "$service_name"; then
      echo "Service $service_name did not become active after restart." >&2
      return 1
    fi
    if [ "$include_lcd" -eq 1 ] && lcd_systemd_unit_present "$service_name"; then
      local lcd_service="lcd-$service_name"
      _check_service_active "$lcd_service" "LCD" || return 1
    fi
    if [ "$include_rfid" -eq 1 ]; then
      local rfid_service="rfid-$service_name"
      _check_service_active "$rfid_service" "RFID" || return 1
    fi
    if [ "$include_camera" -eq 1 ]; then
      local camera_service="camera-$service_name"
      _check_service_active "$camera_service" "Camera" || return 1
    fi
    return 0
  fi

  nohup ./start.sh >/dev/null 2>&1 &
  echo "Services restart triggered"
  return 0
}

restart_lcd_service() {
  local service_name="$1"

  if [ -z "$service_name" ]; then
    return 0
  fi

  if [ ${#SYSTEMCTL_CMD[@]} -eq 0 ] || [ "$SERVICE_MANAGEMENT_MODE" != "$ARTHEXIS_SERVICE_MODE_SYSTEMD" ]; then
    return 0
  fi

  local lcd_service
  lcd_service="lcd-$service_name"

  if ! "${SYSTEMCTL_CMD[@]}" list-unit-files | awk '{print $1}' | grep -Fxq "${lcd_service}.service"; then
    return 0
  fi

  echo "Restarting LCD service ${lcd_service}..."
  "${SYSTEMCTL_CMD[@]}" restart "$lcd_service" || return 1
  wait_for_service_active "$lcd_service" 1
}

clear_workdir_before_restart() {
  local work_dir="$BASE_DIR/work"

  if [ -d "$work_dir" ]; then
    echo "Clearing work directory before restart..."
    find "$work_dir" -mindepth 1 -exec rm -rf -- {} +
  fi
}

  clear_lcd_lockfiles() {
    local lock_dir="$LOCK_DIR"

    if [ -z "$lock_dir" ]; then
      return 0
    fi

    local feature_lock_file="$lock_dir/${ARTHEXIS_LCD_LOCK:-lcd_screen.lck}"

    local -a lcd_lock_files=(
      "$lock_dir/lcd-high"
      "$lock_dir/lcd-low"
      "$lock_dir/${ARTHEXIS_LCD_LOCK:-lcd_screen.lck}"
      "$lock_dir/lcd_screen_enabled.lck"
    )

    local lock_file cleared preserved_disabled_flag
    cleared=0
    preserved_disabled_flag=0
    for lock_file in "${lcd_lock_files[@]}"; do
      if [ -e "$lock_file" ]; then
        if [ "$lock_file" = "$feature_lock_file" ] && grep -qi '^state=disabled' "$lock_file"; then
          preserved_disabled_flag=1
          continue
        fi

        : > "$lock_file"
        cleared=1
      fi
    done

    if [ "$cleared" -eq 1 ]; then
      echo "Cleared LCD lock files before restart."
    fi

    if [ "$preserved_disabled_flag" -eq 1 ]; then
      echo "Preserved LCD disable flag during lock file cleanup."
    fi
  }

upgrade_failure_recovery() {
  local exit_code=$?

  trap - ERR
  set +e

  echo "Upgrade failed with exit code ${exit_code}; attempting to restore services..." >&2

  if [[ $NO_RESTART -eq 1 ]]; then
    echo "Automatic recovery skipped because --no-start/--no-restart was provided." >&2
    exit "$exit_code"
  fi

  if [[ ${SERVICE_ACTIVITY_KNOWN:-0} -eq 1 ]] && [[ ${SERVICE_WAS_ACTIVE:-1} -eq 0 ]] && [[ $FORCE_START -eq 0 ]]; then
    echo "Automatic recovery skipped because services were stopped before the upgrade." >&2
    exit "$exit_code"
  fi

  cleanup_upgrade_progress_lock
  if ! restart_services; then
    echo "Automatic recovery could not restore services; manual intervention required." >&2
  fi

  exit "$exit_code"
}

trap 'upgrade_failure_recovery' ERR

versions_share_minor() {
  local first="$1"
  local second="$2"

  local first_major=""
  local first_minor=""
  local second_major=""
  local second_minor=""

  if [[ $first =~ ^([0-9]+)\.([0-9]+) ]]; then
    first_major="${BASH_REMATCH[1]}"
    first_minor="${BASH_REMATCH[2]}"
  else
    return 1
  fi

  if [[ $second =~ ^([0-9]+)\.([0-9]+) ]]; then
    second_major="${BASH_REMATCH[1]}"
    second_minor="${BASH_REMATCH[2]}"
  else
    return 1
  fi

  if [[ $first_major == "$second_major" && $first_minor == "$second_minor" ]]; then
    return 0
  fi

  return 1
}

confirm_database_deletion() {
  local action="$1"
  local -a targets=()
  local -A seen=()

  while IFS= read -r -d '' path; do
    local name="$(basename "$path")"
    if [[ -z ${seen[$name]:-} ]]; then
      targets+=("$name")
      seen[$name]=1
    fi
  done < <(find "$BASE_DIR" -maxdepth 1 \( -type f -o -type l \) \( -name 'db.sqlite3*' -o -name 'db_*.sqlite3*' \) -print0 2>/dev/null)

  if [ ${#targets[@]} -eq 0 ] || [[ $NO_WARN -eq 1 ]]; then
    return 0
  fi

  if ! can_prompt_for_confirmation; then
    echo "Warning: $action will delete database files, but interactive confirmation is unavailable in this session." >&2
    echo "Re-run in the foreground or pass --no-warn to allow non-interactive execution." >&2
    return 1
  fi

  echo "Warning: $action will delete the following database files without creating a backup:"
  local target
  for target in "${targets[@]}"; do
    echo "  - $target"
  done
  echo "Use --no-warn to bypass this prompt."
  local response
  read -r -p "Continue? [y/N] " response
  if [[ ! $response =~ ^[Yy]$ ]]; then
    return 1
  fi

  return 0
}

can_prompt_for_confirmation() {
  if ! [ -t 0 ]; then
    return 1
  fi

  local process_state=""
  process_state="$(ps -o stat= -p "$$" 2>/dev/null | tr -d '[:space:]')" || return 1

  [[ "$process_state" == *"+"* ]]
}

NODE_ROLE_NAME=$(determine_node_role)

cleanup_non_terminal_git_state "$NODE_ROLE_NAME"

# Determine current and remote versions
if [[ -n "$REQUESTED_BRANCH" ]]; then
  BRANCH="$REQUESTED_BRANCH"
  if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
    git switch "$BRANCH" >/dev/null
  elif git show-ref --verify --quiet "refs/remotes/origin/$BRANCH"; then
    git switch -c "$BRANCH" "origin/$BRANCH" >/dev/null
  else
    echo "Requested branch $BRANCH not found locally or on origin; continuing without switching." >&2
  fi
else
  BRANCH=$(git rev-parse --abbrev-ref HEAD)
  if [[ "$BRANCH" == "HEAD" ]]; then
    echo "Detected detached HEAD; attempting to switch back to the tracked branch..." >&2

    determine_default_branch() {
      local remote_head
      remote_head=$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)
      if [[ -n "$remote_head" ]]; then
        echo "${remote_head#origin/}"
        return 0
      fi

      git branch --remotes --contains HEAD 2>/dev/null \
        | sed -n 's#^[ *]*origin/##p' \
        | head -n1
    }

    TARGET_BRANCH=$(determine_default_branch)
    if [[ -z "$TARGET_BRANCH" ]]; then
      echo "Unable to determine branch to switch to while detached." >&2
      echo "Continuing in detached HEAD state; upgrade steps will run without switching branches." >&2
      BRANCH="HEAD"
    else
      if git show-ref --verify --quiet "refs/heads/$TARGET_BRANCH"; then
        git switch "$TARGET_BRANCH" >/dev/null
      else
        git switch -c "$TARGET_BRANCH" "origin/$TARGET_BRANCH" >/dev/null
      fi

      BRANCH="$TARGET_BRANCH"
      echo "Switched to branch $BRANCH." >&2
    fi
  fi
fi
LOCAL_VERSION="0"
[ -f VERSION ] && LOCAL_VERSION=$(tr -d '\r\n' < VERSION)
LOCAL_REVISION="$(git rev-parse HEAD 2>/dev/null || echo "")"
UPGRADE_INITIAL_VERSION="$LOCAL_VERSION"
UPGRADE_INITIAL_REVISION="$LOCAL_REVISION"
if [[ $RERUN_AFTER_SELF_UPDATE -eq 1 ]]; then
  if [[ -n "$RERUN_INITIAL_VERSION" ]]; then
    UPGRADE_INITIAL_VERSION="$RERUN_INITIAL_VERSION"
  fi
  if [[ -n "$RERUN_INITIAL_REVISION" ]]; then
    UPGRADE_INITIAL_REVISION="$RERUN_INITIAL_REVISION"
  fi
fi

UPGRADE_REVERT_LOCK="$LOCK_DIR/upgrade_revert_revision.lck"
if [[ $REVERT_UPGRADE -eq 1 ]]; then
  if [ ! -f "$UPGRADE_REVERT_LOCK" ]; then
    echo "No previous upgrade revision is recorded; cannot revert." >&2
    exit 1
  fi

  REVERT_TARGET_REVISION="$(tr -d '\r\n' < "$UPGRADE_REVERT_LOCK")"
  if [[ -z "$REVERT_TARGET_REVISION" ]]; then
    echo "Recorded revert revision is empty; cannot revert." >&2
    exit 1
  fi

  if ! git rev-parse --verify "${REVERT_TARGET_REVISION}^{commit}" >/dev/null 2>&1; then
    echo "Recorded revert revision ${REVERT_TARGET_REVISION} is not available locally; cannot revert." >&2
    exit 1
  fi

  LOCAL_ONLY=1
fi

REMOTE_VERSION="$LOCAL_VERSION"
REMOTE_REVISION="$LOCAL_REVISION"
POST_UPGRADE_HOOKS_PENDING=0
if arthexis_has_post_upgrade_hooks "$BASE_DIR" "$LOCK_DIR"; then
  POST_UPGRADE_HOOKS_PENDING=1
fi
if [[ $LOCAL_ONLY -eq 1 ]]; then
  echo "Local refresh requested; skipping remote update check."
elif target_pin_requested; then
  if [[ $PRE_CHECK -ne 1 ]]; then
    reset_safe_git_changes "$NODE_ROLE_NAME"
  fi
  echo "Resolving pinned release target..."
  resolve_pinned_release_target
else
  if [[ $PRE_CHECK -ne 1 ]]; then
    reset_safe_git_changes "$NODE_ROLE_NAME"
  fi
  echo "Checking repository for updates..."
  if fetch_branch_with_ref_repair origin "$BRANCH"; then
    REMOTE_REVISION="$(git rev-parse "origin/$BRANCH" 2>/dev/null || echo "$REMOTE_REVISION")"
    if git cat-file -e "origin/$BRANCH:VERSION" 2>/dev/null; then
      REMOTE_VERSION=$(git show "origin/$BRANCH:VERSION" | tr -d '\r\n')
    fi
  else
    switched_to_default_branch=0
    if [[ $FORCE_UPGRADE -eq 1 && "$BRANCH" != "main" ]]; then
      echo "Unable to fetch origin/$BRANCH while --force was provided; attempting fallback to origin/main before using local sources." >&2
      if fetch_branch_with_ref_repair origin main; then
        if git show-ref --verify --quiet "refs/heads/main"; then
          git switch main >/dev/null
          git branch --set-upstream-to=origin/main main >/dev/null 2>&1 || true
        else
          git switch -c main origin/main >/dev/null
        fi

        BRANCH="main"
        switched_to_default_branch=1
        LOCAL_REVISION="$(git rev-parse HEAD 2>/dev/null || echo "$LOCAL_REVISION")"
        if [ -f VERSION ]; then
          LOCAL_VERSION="$(tr -d '\r\n' < VERSION)"
        fi
        REMOTE_REVISION="$(git rev-parse "origin/$BRANCH" 2>/dev/null || echo "$LOCAL_REVISION")"
        if git cat-file -e "origin/$BRANCH:VERSION" 2>/dev/null; then
          REMOTE_VERSION=$(git show "origin/$BRANCH:VERSION" | tr -d '\r\n')
        else
          REMOTE_VERSION="$LOCAL_VERSION"
        fi
      fi
    fi

    if [[ $switched_to_default_branch -eq 0 ]]; then
      echo "Error: Unable to reach the repository to check for updates." >&2
      if [[ $FORCE_UPGRADE -eq 1 ]]; then
        echo "Warning: Continuing upgrade with local sources because --force was provided." >&2
        REMOTE_REVISION="$LOCAL_REVISION"
        REMOTE_VERSION="$LOCAL_VERSION"
        LOCAL_ONLY=1
      elif [[ $POST_UPGRADE_HOOKS_PENDING -eq 1 ]]; then
        echo "Warning: Continuing with local sources to retry pending post-upgrade hooks." >&2
        REMOTE_REVISION="$LOCAL_REVISION"
        REMOTE_VERSION="$LOCAL_VERSION"
        LOCAL_ONLY=1
      else
        echo "Info: Re-run upgrade.sh with --local or --force to proceed without remote updates." >&2
        exit 1
      fi
    fi
  fi
fi

UPGRADE_NEEDED=0
if [[ "$LOCAL_VERSION" != "$REMOTE_VERSION" ]]; then
  UPGRADE_NEEDED=1
elif [[ -n "$REMOTE_REVISION" && -n "$LOCAL_REVISION" && "$LOCAL_REVISION" != "$REMOTE_REVISION" ]]; then
  UPGRADE_NEEDED=1
elif [[ $RERUN_AFTER_SELF_UPDATE -eq 1 ]]; then
  UPGRADE_NEEDED=1
elif [[ $LOCAL_ONLY -eq 1 ]]; then
  UPGRADE_NEEDED=1
elif [[ $POST_UPGRADE_HOOKS_PENDING -eq 1 ]]; then
  UPGRADE_NEEDED=1
fi

UPGRADE_PLANNED=1
if [[ "$LOCAL_VERSION" == "$REMOTE_VERSION" ]]; then
  if [[ $REVERT_UPGRADE -eq 1 ]]; then
    echo "Reverting working tree to revision $REVERT_TARGET_REVISION."
  elif [[ $LOCAL_ONLY -eq 1 ]]; then
    echo "Proceeding with local refresh despite matching version $LOCAL_VERSION."
  elif [[ $RERUN_AFTER_SELF_UPDATE -eq 1 ]]; then
    echo "Detected prior upgrade.sh update; continuing upgrade for $REMOTE_VERSION despite matching versions."
  elif [[ "$CHANNEL" == "unstable" ]]; then
    echo "Latest/unstable channel requested; continuing upgrade despite matching version $REMOTE_VERSION."
  elif [[ $FORCE_UPGRADE -eq 1 ]]; then
    echo "Forcing upgrade despite matching version $LOCAL_VERSION."
  elif [[ -n "$REMOTE_REVISION" && -n "$LOCAL_REVISION" && "$LOCAL_REVISION" != "$REMOTE_REVISION" ]]; then
    if target_pin_requested; then
      echo "Pinned release target requested; aligning working tree to $REMOTE_REVISION for version $REMOTE_VERSION."
    elif [[ $POST_UPGRADE_HOOKS_PENDING -eq 1 ]]; then
      echo "Pending post-upgrade hooks detected; retrying against current checkout before same-version updates."
      LOCAL_ONLY=1
      REMOTE_REVISION="$LOCAL_REVISION"
    else
      if [[ $CHECK_ONLY -eq 1 ]]; then
        print_pending_commit_messages "$LOCAL_REVISION" "$REMOTE_REVISION"
      fi
      echo "Updates detected for version $LOCAL_VERSION, but --latest is required to apply them."
      echo "Re-run upgrade.sh with --latest to migrate to the newest changes."
      exit 0
    fi
  elif [[ $POST_UPGRADE_HOOKS_PENDING -eq 1 ]]; then
    echo "Pending post-upgrade hooks detected; continuing local refresh to retry them."
    LOCAL_ONLY=1
    REMOTE_REVISION="$LOCAL_REVISION"
  else
    echo "Already on version $LOCAL_VERSION; skipping upgrade."
    exit 0
  fi
fi

if [[ $CHECK_ONLY -eq 1 && $POST_UPGRADE_HOOKS_PENDING -eq 1 ]]; then
  LOCAL_ONLY=1
  REMOTE_REVISION="$LOCAL_REVISION"
fi

if ! target_pin_requested; then
  auto_realign_branch_for_role "$NODE_ROLE_NAME" "$BRANCH"
fi

# Track if the node is installed (virtual environment present)
VENV_PRESENT=1
[ -d .venv ] || VENV_PRESENT=0

SERVICE_WAS_ACTIVE=0
SERVICE_ACTIVITY_KNOWN=0
if service_was_active "$SERVICE_NAME"; then
  SERVICE_WAS_ACTIVE=1
  SERVICE_ACTIVITY_KNOWN=1
else
  case $? in
    1)
      SERVICE_ACTIVITY_KNOWN=1
      ;;
    2)
      SERVICE_WAS_ACTIVE=1
      ;;
  esac
fi
if [[ $RERUN_AFTER_SELF_UPDATE -eq 1 ]] && [[ ${RERUN_SERVICE_WAS_ACTIVE:-0} -eq 1 ]]; then
  SERVICE_WAS_ACTIVE=1
  SERVICE_ACTIVITY_KNOWN=1
fi
LCD_WAS_ACTIVE=0
if lcd_service_was_active "$SERVICE_NAME"; then
  LCD_WAS_ACTIVE=1
elif [[ $RERUN_AFTER_SELF_UPDATE -eq 1 ]] && [[ ${RERUN_LCD_WAS_ACTIVE:-0} -eq 1 ]]; then
  LCD_WAS_ACTIVE=1
fi
LCD_RESTART_REQUIRED=$LCD_WAS_ACTIVE
LCD_RESTART_COMPLETED=0

stop_running_instance() {
  local skip_if_inactive="${1:-0}"

  if [[ $skip_if_inactive -eq 1 ]] && [[ ${SERVICE_ACTIVITY_KNOWN:-0} -eq 1 ]] && [[ ${SERVICE_WAS_ACTIVE:-0} -eq 0 ]]; then
    echo "Services are not running; nothing to stop."
    return 0
  fi

  if [[ $CHECK_ONLY -ne 1 ]] && [[ $VENV_PRESENT -eq 1 ]]; then
    echo "Stopping running instance..."
    STOP_ARGS=(--all)
    if [[ $FORCE_STOP -eq 1 || $STOP_ONLY -eq 0 ]]; then
      STOP_ARGS+=(--force)
    fi
    if [[ $STOP_CONFIRM -eq 1 ]]; then
      STOP_ARGS+=(--confirm)
    fi
    if ! ARTHEXIS_SKIP_LCD_STOP=1 ./stop.sh "${STOP_ARGS[@]}"; then
      if [[ $FORCE_STOP -eq 1 || $STOP_ONLY -eq 0 ]]; then
        echo "Upgrade aborted even after forcing stop. Resolve active charging sessions before retrying." >&2
      else
        echo "Upgrade aborted because active charging sessions are in progress. Resolve active charging sessions before retrying." >&2
      fi
      exit 1
    fi
  elif [[ $CHECK_ONLY -ne 1 ]]; then
    echo "Virtual environment missing; deferring service-stop guard until after environment bootstrap."
    return 2
  fi
}

SERVICE_STOPPED_FOR_UPGRADE=0
ensure_services_stopped_for_upgrade() {
  if [[ $SERVICE_STOPPED_FOR_UPGRADE -eq 1 ]]; then
    return 0
  fi

  local stop_status=0

  if stop_running_instance 0; then
    stop_status=0
  else
    stop_status=$?
  fi

  case $stop_status in
    0)
      SERVICE_STOPPED_FOR_UPGRADE=1
      ;;
    2)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

DEFER_BROADCAST_MESSAGE=0
if [[ $SERVICE_WAS_ACTIVE -eq 1 ]] && [[ $UPGRADE_PLANNED -eq 1 ]]; then
  if [[ -n "$LOCAL_REVISION" || -n "$REMOTE_REVISION" ]]; then
    DEFER_BROADCAST_MESSAGE=1
  fi
fi

# Stop running instance only if the node is installed
if [[ $STOP_ONLY -eq 1 ]]; then
  if stop_running_instance 1; then
    :
  else
    case $? in
      2)
        ;;
      *)
        exit 1
        ;;
    esac
  fi
  exit 0
fi

# Ensure the active-session stop guard runs before any irreversible upgrade
# changes to the repository or runtime environment.
if [[ $CHECK_ONLY -ne 1 ]] && [[ $REVERT_UPGRADE -eq 0 ]] && [[ $VENV_PRESENT -eq 1 ]]; then
  ensure_services_stopped_for_upgrade
fi

if [[ $REVERT_UPGRADE -eq 1 ]]; then
  ensure_services_stopped_for_upgrade
  if [[ $CHECK_ONLY -eq 1 ]]; then
    echo "Check mode enabled; skipping revert reset."
  else
    stash_local_changes_for_upgrade
    if [[ -n "$LOCAL_REVISION" ]]; then
      printf '%s\n' "$LOCAL_REVISION" > "$UPGRADE_REVERT_LOCK"
    fi

    if [[ "$LOCAL_REVISION" == "$REVERT_TARGET_REVISION" ]]; then
      echo "Already at revision $REVERT_TARGET_REVISION; no git reset required."
    else
      echo "Resetting repository to $REVERT_TARGET_REVISION..."
      git reset --hard "$REVERT_TARGET_REVISION"
    fi
  fi
fi

# Apply repository updates.
if [[ $LOCAL_ONLY -eq 1 ]]; then
  echo "Skipping git pull for local refresh."
  if [[ $CHECK_ONLY -eq 1 ]]; then
    echo "Upgrade check complete; no remote updates were pulled."
    restore_stashed_changes_after_upgrade
    exit 0
  fi
else
  if target_pin_requested; then
    echo "Applying pinned release target $REMOTE_REVISION..."
  else
    echo "Pulling latest changes..."
  fi
  stash_local_changes_for_upgrade
  if [[ $CHECK_ONLY -ne 1 ]] && [[ -n "$LOCAL_REVISION" ]]; then
    if [[ $RERUN_AFTER_SELF_UPDATE -eq 1 ]] && [[ -s "$UPGRADE_REVERT_LOCK" ]]; then
      echo "Preserving existing revert target $UPGRADE_REVERT_LOCK during self-update rerun."
    else
      printf '%s\n' "$LOCAL_REVISION" > "$UPGRADE_REVERT_LOCK"
    fi
  fi
  if target_pin_requested; then
    if [[ $CHECK_ONLY -ne 1 ]]; then
      git reset --hard "$REMOTE_REVISION"
    fi
  else
    git pull --rebase
    REMOTE_REVISION="$(git rev-parse HEAD 2>/dev/null || echo "$REMOTE_REVISION")"
  fi
  refresh_dependency_change_state

  # If the upgrade script itself was updated, stop so the new version is executed on the next run.
  POST_PULL_UPGRADE_HASH=""
  if [ -f "$UPGRADE_SCRIPT_PATH" ]; then
    POST_PULL_UPGRADE_HASH="$(sha256sum "$UPGRADE_SCRIPT_PATH" | awk '{print $1}')"
  fi
  if [ -n "$INITIAL_UPGRADE_HASH" ] && [ -n "$POST_PULL_UPGRADE_HASH" ] && \
     [ "$POST_PULL_UPGRADE_HASH" != "$INITIAL_UPGRADE_HASH" ]; then
    if [[ $CHECK_ONLY -eq 1 ]]; then
      echo "upgrade.sh was updated during git pull; run ./upgrade.sh without --check to continue with the new script."
      restore_stashed_changes_after_upgrade
      exit 0
    fi

    {
      printf 'REMOTE_VERSION=%s\n' "$REMOTE_VERSION"
      printf 'LOCAL_VERSION=%s\n' "$UPGRADE_INITIAL_VERSION"
      printf 'LOCAL_REVISION=%s\n' "$UPGRADE_INITIAL_REVISION"
      printf 'SERVICE_WAS_ACTIVE=%s\n' "$SERVICE_WAS_ACTIVE"
      printf 'LCD_WAS_ACTIVE=%s\n' "$LCD_WAS_ACTIVE"
      printf 'UPGRADE_STASH_REF=%s\n' "$UPGRADE_STASH_REF"
      printf 'UPGRADE_STASH_CREATED=%s\n' "$UPGRADE_STASH_CREATED"
    } > "$UPGRADE_RERUN_LOCK"
    notify_lcd_manual_upgrade_required
    start_lcd_upgrade_helper_service
    if rerun_with_updated_script; then
      exit 0
    else
      echo "upgrade.sh was updated during git pull; please run the upgrade again to use the new script." >&2
      exit "$UPGRADE_RERUN_EXIT_CODE"
    fi
  fi

  if [[ $CHECK_ONLY -eq 1 ]]; then
    echo "Upgrade check complete; upgrade.sh did not change. No follow-up upgrade run is required for the script itself."
    restore_stashed_changes_after_upgrade
    exit 0
  fi
fi

# Normalize VERSION by removing any trailing development markers.
arthexis_update_version_marker "$BASE_DIR"

# Create virtual environment automatically if missing
if [ $VENV_PRESENT -eq 0 ]; then
  if ensure_virtualenv; then
    VENV_PRESENT=1
  else
    echo "Virtual environment not found and automatic creation failed. Run ./install.sh to install the node." >&2
    exit 1
  fi
fi

# Ensure Python dependencies and database schema stay aligned with the
# freshly-pulled code before refreshing runtime data.
if [ $VENV_PRESENT -eq 1 ]; then
  if [[ $DEPENDENCY_REFRESH_REQUIRED -eq 1 ]]; then
    echo "Dependency changes detected; stopping services before modifying the runtime environment."
    ensure_services_stopped_for_upgrade
  fi
  # shellcheck disable=SC1091
  source .venv/bin/activate
  PYTHON_BIN="$VIRTUAL_ENV/bin/python"
  pip_install_env=()
  pip_install_flags=()
  if pip_requires_break_system_packages python; then
    pip_install_env+=("PIP_BREAK_SYSTEM_PACKAGES=1")
    pip_install_flags+=("--break-system-packages")
  fi
  if [[ $DEPENDENCY_REFRESH_REQUIRED -eq 0 ]]; then
    echo "Dependencies unchanged; skipping pip bootstrap."
    arthexis_timing_record "pip_bootstrap" 0 "skipped"
  else
    arthexis_timing_start "pip_bootstrap"
    env "${pip_install_env[@]}" python -m pip install --upgrade pip "${pip_install_flags[@]}"
    arthexis_timing_end "pip_bootstrap"
  fi
  # env-refresh.sh is responsible for syncing requirements and updating the
  # requirements.sha256 lock file; avoid duplicating that work here.
  if [[ $DEFER_BROADCAST_MESSAGE -eq 1 ]]; then
    if ! broadcast_upgrade_start_net_message "$LOCAL_REVISION" "$REMOTE_REVISION"; then
      echo "Warning: failed to broadcast upgrade Net Message" >&2
    fi
  fi
  deactivate
else
  arthexis_timing_record "pip_bootstrap" 0 "skipped"
fi

# Remove existing database if requested
if [ "$CLEAN" -eq 1 ]; then
  if ! confirm_database_deletion "Running upgrade with --clean"; then
    echo "Upgrade aborted by user."
    exit 1
  fi
  ensure_services_stopped_for_upgrade
  rm -f db.sqlite3 db.sqlite3* 2>/dev/null || true
  rm -f db_*.sqlite3* 2>/dev/null || true
fi

# Refresh environment and restart service
build_env_refresh_args() {
  ENV_ARGS=""
  if [[ "$CHANNEL" == "unstable" ]]; then
    ENV_ARGS="--latest"
  fi
  if [[ $FORCE_ENV_REFRESH -eq 1 ]]; then
    ENV_ARGS="$ENV_ARGS --force-refresh"
  fi
  if [[ $MIGRATE_RECONCILE -eq 1 ]]; then
    ENV_ARGS="$ENV_ARGS --migrate"
  fi
  if [[ $AUTO_RECONCILE_ON_MISMATCH -eq 1 ]]; then
    ENV_ARGS="$ENV_ARGS --reconcile"
  fi
}

pending_migrations_after_update() {
  local migrations_output

  if [[ -z "${PYTHON_BIN:-}" || ! -x "$PYTHON_BIN" ]]; then
    return 0
  fi

  if ! migrations_output="$("$PYTHON_BIN" manage.py showmigrations --list 2>/dev/null)"; then
    return 0
  fi

  printf '%s\n' "$migrations_output" | grep -q '^[[:space:]]*\[ \]'
}

missing_migrations_after_update() {
  if [[ -z "${PYTHON_BIN:-}" || ! -x "$PYTHON_BIN" ]]; then
    return 0
  fi

  if "$PYTHON_BIN" manage.py makemigrations --check --dry-run >/dev/null 2>&1; then
    return 1
  fi

  return 0
}

role_app_profiles_explicitly_enabled_for_upgrade() {
  case "${ARTHEXIS_ROLE_APP_PROFILES:-}" in
    1|true|TRUE|True|yes|YES|Yes|on|ON|On)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

role_app_profile_inputs_present_for_upgrade() {
  [[ -n "${ARTHEXIS_ROLE_APP_FEATURE_PACKS:-}" ]] && return 0
  [[ -n "${ARTHEXIS_FEATURE_PACKS:-}" ]] && return 0
  [[ -n "${ARTHEXIS_ROLE_APP_DISABLED_APPS:-}" ]] && return 0
  [[ -n "${ARTHEXIS_DISABLED_APPS:-}" ]] && return 0
  return 1
}

role_app_lock_refresh_explicitly_enabled_for_upgrade() {
  case "${ARTHEXIS_ROLE_APP_LOCK_REFRESH:-}" in
    1|true|TRUE|True|yes|YES|Yes|on|ON|On)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

role_app_lock_preserve_direct_enabled_for_upgrade() {
  case "${ARTHEXIS_ROLE_APP_LOCK_PRESERVE_DIRECT:-1}" in
    1|true|TRUE|True|yes|YES|Yes|on|ON|On)
      return 0
      ;;
    0|false|FALSE|False|no|NO|No|off|OFF|Off)
      return 1
      ;;
    *)
      return 0
      ;;
  esac
}

role_enabled_apps_lock_refresh_required() {
  local node_role="${1:-}"

  case "${node_role,,}" in
    control)
      [[ -f "$LOCK_DIR/enabled_apps.lck" ]] && return 0
      ;;
    satellite|terminal|watchtower|constellation)
      if [[ -f "$LOCK_DIR/enabled_apps.lck" ]]; then
        role_app_lock_refresh_explicitly_enabled_for_upgrade && return 0
        role_app_profile_inputs_present_for_upgrade && return 0
        return 1
      fi
      ;;
    *)
      return 1
      ;;
  esac

  role_app_profiles_explicitly_enabled_for_upgrade && return 0
  role_app_profile_inputs_present_for_upgrade && return 0

  return 1
}

existing_enabled_apps_lock_direct_includes() {
  local base_dir="${1:-$BASE_DIR}"
  local node_role="${2:-$NODE_ROLE_NAME}"

  if [[ ! -f "$LOCK_DIR/enabled_apps.lck" ]]; then
    return 0
  fi

  "$PYTHON_BIN" - "$base_dir" "$node_role" <<'PY'
import sys
from pathlib import Path

from config.settings.apps import _BUILT_IN_APP_ENTRIES, _normalize_selected_app_entries
from utils.enabled_apps_lock import (
    read_enabled_apps_lock_direct_entries,
    read_enabled_apps_lock_direct_sources,
)


def aliases(selector):
    normalized = selector.strip()
    if not normalized:
        return set()
    values = {normalized}
    if normalized.startswith("apps."):
        values.add(normalized.removeprefix("apps."))
        values.add(normalized.rsplit(".", maxsplit=1)[-1])
    elif "." not in normalized:
        values.add(f"apps.{normalized}")
    return values


def selector_is_known(selector):
    known_entries = set(_BUILT_IN_APP_ENTRIES)
    normalized_entries = _normalize_selected_app_entries((selector,), _BUILT_IN_APP_ENTRIES)
    return bool(normalized_entries) and all(
        entry in known_entries for entry in normalized_entries
    )


base_dir = Path(sys.argv[1])
direct_sources = read_enabled_apps_lock_direct_sources(base_dir)
source_selectors = set(direct_sources)
charger_route_locks_present = (
    (base_dir / ".locks" / "charger_facing.lck").exists()
    or (base_dir / ".locks" / "ocpp_gateway.lck").exists()
)
charger_facing_route_selectors = {"apps.ocpp"}
for selector in sorted(read_enabled_apps_lock_direct_entries(base_dir) or ()):
    if not selector_is_known(selector):
        continue
    selector_aliases = aliases(selector)
    if selector in direct_sources or selector_aliases & source_selectors:
        continue
    normalized_entries = set(
        _normalize_selected_app_entries((selector,), _BUILT_IN_APP_ENTRIES)
    )
    if charger_route_locks_present and normalized_entries & charger_facing_route_selectors:
        continue
    print(selector)
PY
}

restore_charger_facing_enabled_apps_lock_metadata() {
  local base_dir="${1:-$BASE_DIR}"

  if [[ ! -f "$LOCK_DIR/enabled_apps.lck" ]]; then
    return 0
  fi

  "$PYTHON_BIN" - "$base_dir" <<'PY'
import sys
from pathlib import Path

from utils.enabled_apps_lock import (
    read_enabled_apps_lock,
    read_enabled_apps_lock_direct_entries,
    read_enabled_apps_lock_direct_sources,
    write_enabled_apps_lock,
)

base_dir = Path(sys.argv[1])
lock_dir = base_dir / ".locks"
if not (
    (lock_dir / "charger_facing.lck").exists()
    or (lock_dir / "ocpp_gateway.lck").exists()
):
    raise SystemExit(0)

enabled_entries = read_enabled_apps_lock(base_dir) or set()
if "apps.ocpp" not in enabled_entries and "ocpp" not in enabled_entries:
    raise SystemExit(0)

direct_entries = set(read_enabled_apps_lock_direct_entries(base_dir) or ())
direct_sources = read_enabled_apps_lock_direct_sources(base_dir)
ocpp_direct_selectors = {
    selector for selector in direct_entries if selector in {"apps.ocpp", "ocpp"}
}
if not ocpp_direct_selectors:
    direct_entries.add("apps.ocpp")
    direct_sources["apps.ocpp"] = "charger-facing"
write_enabled_apps_lock(
    enabled_entries,
    base_dir,
    direct_apps=direct_entries,
    direct_app_sources=direct_sources,
)
PY
}

refresh_role_enabled_apps_lock() {
  local node_role="${1:-}"

  if ! role_enabled_apps_lock_refresh_required "$node_role"; then
    return 0
  fi

  if [[ -z "${PYTHON_BIN:-}" || ! -x "$PYTHON_BIN" ]]; then
    echo "Role enabled-apps lock refresh requires an executable Python interpreter." >&2
    return 1
  fi

  echo "Refreshing ${node_role} enabled-apps lock..."
  local status
  local direct_includes=""
  local include_args=()
  local selector
  if role_app_lock_preserve_direct_enabled_for_upgrade; then
    if ! direct_includes="$(existing_enabled_apps_lock_direct_includes "$BASE_DIR" "$node_role")"; then
      echo "Unable to inspect existing enabled-apps lock direct entries." >&2
      return 1
    fi
  fi
  while IFS= read -r selector; do
    [[ -n "$selector" ]] && include_args+=(--include "$selector")
  done <<< "$direct_includes"
  arthexis_timing_start "enabled_apps_lock"
  if "$PYTHON_BIN" manage.py enabled_apps_lock --role="$node_role" --strict --write --preserve-application-disables "${include_args[@]}"; then
    if restore_charger_facing_enabled_apps_lock_metadata "$BASE_DIR"; then
      arthexis_timing_end "enabled_apps_lock"
      return 0
    else
      status=$?
      arthexis_timing_end "enabled_apps_lock" "failed"
      return "$status"
    fi
  else
    status=$?
    arthexis_timing_end "enabled_apps_lock" "failed"
    return "$status"
  fi
}

risky_files_changed_for_pre_stop_refresh() {
  local changed_paths

  if [[ -z "$LOCAL_REVISION" || -z "$REMOTE_REVISION" || "$LOCAL_REVISION" == "$REMOTE_REVISION" ]]; then
    return 1
  fi

  changed_paths="$(git diff --name-only "$LOCAL_REVISION" "$REMOTE_REVISION" 2>/dev/null)" || return 0
  printf '%s\n' "$changed_paths" | grep -Eq '(^requirements[^/]*\.txt$|^env-refresh\.(sh|py)$|^scripts/|/migrations/|^data/|/fixtures/)'
}

can_refresh_environment_before_stop() {
  if [[ $CHECK_ONLY -eq 1 || $CLEAN -eq 1 ]]; then
    return 1
  fi
  if [[ $MIGRATE_RECONCILE -eq 1 || $AUTO_RECONCILE_ON_MISMATCH -eq 1 ]]; then
    return 1
  fi
  if [[ $DEPENDENCY_REFRESH_REQUIRED -eq 1 || $FORCE_ENV_REFRESH -eq 1 ]]; then
    return 1
  fi
  if pending_migrations_after_update; then
    return 1
  fi
  if missing_migrations_after_update; then
    return 1
  fi
  if risky_files_changed_for_pre_stop_refresh; then
    return 1
  fi

  return 0
}

run_env_refresh() {
  local timing_label="${1:-env_refresh}"

  echo "Refreshing environment..."
  arthexis_timing_start "$timing_label"
  FAILOVER_CREATED=1 ./env-refresh.sh $ENV_ARGS
  arthexis_timing_end "$timing_label"
}

run_env_dependency_refresh() {
  local timing_label="${1:-env_refresh_deps}"
  local status

  echo "Refreshing dependencies before enabled-apps lock..."
  arthexis_timing_start "$timing_label"
  if FAILOVER_CREATED=1 ./env-refresh.sh --deps-only $ENV_ARGS; then
    arthexis_timing_end "$timing_label"
    return 0
  else
    status=$?
    arthexis_timing_end "$timing_label" "failed"
    return "$status"
  fi
}

build_env_refresh_args
if role_enabled_apps_lock_refresh_required "$NODE_ROLE_NAME" && \
   [[ $DEPENDENCY_REFRESH_REQUIRED -eq 1 ]]; then
  echo "Dependency changes detected; stopping services before enabled-apps lock dependency refresh."
  ensure_services_stopped_for_upgrade
  if ! run_env_dependency_refresh "env_refresh_deps"; then
    echo "Dependency refresh failed; aborting upgrade before enabled-apps lock refresh." >&2
    exit 1
  fi
fi
if ! refresh_role_enabled_apps_lock "$NODE_ROLE_NAME"; then
  echo "${NODE_ROLE_NAME} enabled-apps lock refresh failed; aborting upgrade." >&2
  exit 1
fi
ENV_REFRESH_COMPLETED_BEFORE_STOP=0
if can_refresh_environment_before_stop; then
  echo "No pending migrations or dependency changes detected; refreshing environment before stopping services."
  run_env_refresh "env_refresh_pre_stop"
  ENV_REFRESH_COMPLETED_BEFORE_STOP=1
else
  ensure_services_stopped_for_upgrade
fi

if [[ $ENV_REFRESH_COMPLETED_BEFORE_STOP -eq 0 ]]; then
  run_env_refresh "env_refresh"
else
  echo "Environment refresh completed before service stop; skipping stopped-service refresh."
  arthexis_timing_record "env_refresh" 0 "completed_pre_stop"
  ensure_services_stopped_for_upgrade
fi

if [ -n "${PYTHON_BIN:-}" ] && ls data/*.json >/dev/null 2>&1; then
  arthexis_timing_start "load_user_data"
  "$PYTHON_BIN" manage.py loaddata data/*.json
  arthexis_timing_end "load_user_data"
elif [ -n "${PYTHON_BIN:-}" ]; then
  arthexis_timing_record "load_user_data" 0 "skipped"
fi

if [[ -n "${PYTHON_BIN:-}" ]]; then
  arthexis_timing_start "release_data_transforms"
  "$PYTHON_BIN" manage.py release run-data-transforms --max-batches 1
  arthexis_timing_end "release_data_transforms"
fi

if [ -n "$SERVICE_NAME" ]; then
  remove_prestart_env_refresh "$SERVICE_NAME"
  if [ -f "$LOCK_DIR/celery.lck" ] && [ "$SERVICE_MANAGEMENT_MODE" = "$ARTHEXIS_SERVICE_MODE_SYSTEMD" ]; then
    remove_prestart_env_refresh "celery-$SERVICE_NAME"
    remove_prestart_env_refresh "celery-beat-$SERVICE_NAME"
  fi
  if lcd_systemd_unit_present "$SERVICE_NAME"; then
    remove_prestart_env_refresh "lcd-$SERVICE_NAME"
  fi
fi

# Reload personal user data fixtures

# Migrate existing systemd unit to dedicated Celery services if needed
if [[ -z "$SERVICE_NAME" ]] && [[ -f "$LOCK_DIR/service.lck" ]]; then
  SERVICE_NAME="$(cat "$LOCK_DIR/service.lck")"
fi
if [ -n "$SERVICE_NAME" ]; then
  SERVICE_FILE="${SYSTEMD_DIR}/${SERVICE_NAME}.service"
  if [ -f "$SERVICE_FILE" ] && grep -Fq "celery -A" "$SERVICE_FILE"; then
    echo "Migrating service configuration for Celery..."
    touch "$LOCK_DIR/celery.lck"
    arthexis_install_service_stack "$BASE_DIR" "$LOCK_DIR" "$SERVICE_NAME" true "$BASE_DIR/scripts/service-start.sh" "$SERVICE_MANAGEMENT_MODE"
  fi
fi

restore_stashed_changes_after_upgrade

CURRENT_REVISION="$(git rev-parse HEAD 2>/dev/null || echo "${REMOTE_REVISION:-}")"
arthexis_run_post_upgrade_hooks "$BASE_DIR" "$LOCK_DIR"

if [[ $CLEAR_LOGS -eq 1 ]]; then
  arthexis_clear_log_files "$BASE_DIR" "${LOG_DIR:-}" "${LOG_FILE:-}"
fi

if [[ $CLEAR_WORK -eq 1 ]]; then
  clear_workdir_before_restart
fi

if [ -n "${PYTHON_BIN:-}" ]; then
  arthexis_timing_start "reconcile_node_features_services"
  "$PYTHON_BIN" manage.py reconcile_node_features_services
  arthexis_timing_end "reconcile_node_features_services"
fi

if [ -n "$SERVICE_NAME" ] && [ "$SERVICE_MANAGEMENT_MODE" = "$ARTHEXIS_SERVICE_MODE_SYSTEMD" ]; then
  if [[ "${NODE_ROLE_NAME,,}" == "control" ]]; then
    arthexis_install_control_usb_polling_timer_overrides "$LOCK_DIR" true
  else
    arthexis_remove_control_usb_polling_timer_overrides
  fi
fi

if [ -n "$SERVICE_NAME" ] && [ "$SERVICE_MANAGEMENT_MODE" = "$ARTHEXIS_SERVICE_MODE_SYSTEMD" ]; then
  if camera_service_configured; then
    arthexis_install_camera_service_unit "$BASE_DIR" "$LOCK_DIR" "$SERVICE_NAME"
  else
    arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "camera-${SERVICE_NAME}.service"
  fi
fi

if [ -n "$SERVICE_NAME" ] && [ "$SERVICE_MANAGEMENT_MODE" = "$ARTHEXIS_SERVICE_MODE_SYSTEMD" ]; then
  arthexis_remove_systemd_unit_if_present "$LOCK_DIR" "lcd-${SERVICE_NAME}.service"
fi

SHOULD_RESTART_AFTER_UPGRADE=1
if [ -n "$SERVICE_NAME" ] && [[ $SERVICE_WAS_ACTIVE -eq 0 ]]; then
  SHOULD_RESTART_AFTER_UPGRADE=0
fi
if [[ $FORCE_START -eq 1 ]]; then
  SHOULD_RESTART_AFTER_UPGRADE=1
fi

if [[ $NO_RESTART -eq 0 ]]; then
  if [[ $SHOULD_RESTART_AFTER_UPGRADE -eq 0 ]]; then
    if [ -n "$SERVICE_NAME" ]; then
      echo "Service $SERVICE_NAME was not running before upgrade; skipping automatic restart."
    else
      echo "Skipping automatic restart because services were not running before upgrade."
    fi
  else
    cleanup_upgrade_progress_lock
    RESTART_LCD_WITH_CORE=0
    if ! restart_services "$RESTART_LCD_WITH_CORE"; then
      echo "Detected failed restart after upgrade." >&2
      echo "Manual intervention required to restore services." >&2
      exit 1
    else
      if [[ $RESTART_LCD_WITH_CORE -eq 1 ]]; then
        LCD_RESTART_REQUIRED=0
      fi
    fi
  fi
fi

if [[ ${LCD_RESTART_COMPLETED:-0} -eq 1 ]]; then
  LCD_RESTART_REQUIRED=0
fi

if [ -n "$SERVICE_NAME" ] && [[ $NO_RESTART -eq 0 ]] && [[ $LCD_RESTART_REQUIRED -eq 1 ]]; then
  if ! restart_lcd_service "$SERVICE_NAME"; then
    echo "LCD service lcd-$SERVICE_NAME did not restart cleanly after upgrade." >&2
    exit 1
  fi
fi

if [ -n "$SERVICE_NAME" ] && [[ $NO_RESTART -eq 0 ]]; then
  arthexis_refresh_suite_uptime_lock "$BASE_DIR" || true
fi
