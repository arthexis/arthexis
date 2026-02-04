#!/usr/bin/env bash
set -euo pipefail

DEFAULT_REPO_DIR="/opt/arthexis"
DEFAULT_ORIGIN_URL="https://github.com/arthexis/arthexis.git"
DEFAULT_BRANCH="main"

ubuntu_launch_usage() {
  local script_name="$1"
  cat <<USAGE
Usage: $script_name [options] [-- install.sh args]

Options:
  --repo-dir PATH     Target repo directory (default: $DEFAULT_REPO_DIR)
  --origin URL        Git origin URL (default: $DEFAULT_ORIGIN_URL)
  --branch NAME       Git branch to checkout (default: $DEFAULT_BRANCH)
  -h, --help          Show this help output

Examples:
  $script_name --repo-dir /opt/arthexis -- --terminal --start
  $script_name --branch main -- --control --service arthexis --start
USAGE
}

ubuntu_launch_warn_if_not_version() {
  local expected_version="$1"
  if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    if [[ "${ID:-}" != "ubuntu" || "${VERSION_ID:-}" != "${expected_version}"* ]]; then
      echo "Warning: This script targets Ubuntu ${expected_version}. Detected ${PRETTY_NAME:-unknown}."
    fi
  fi
}

ubuntu_launch_ensure_sudo() {
  if [[ "$EUID" -ne 0 ]]; then
    if ! command -v sudo >/dev/null 2>&1; then
      echo "sudo is required to install system dependencies." >&2
      exit 1
    fi
    echo "sudo"  # shellcheck disable=SC2124
  fi
}

ubuntu_launch_install_packages() {
  local sudo_cmd
  sudo_cmd=$(ubuntu_launch_ensure_sudo)

  $sudo_cmd apt-get update
  $sudo_cmd apt-get install -y \
    ca-certificates \
    curl \
    git \
    python3 \
    python3-venv \
    python3-pip \
    python3-dev \
    build-essential \
    libpq-dev \
    nginx \
    redis-server
}

ubuntu_launch_prepare_repo() {
  local repo_dir="$1"
  local origin_url="$2"
  local branch="$3"

  if [[ -d "$repo_dir/.git" ]]; then
    git -C "$repo_dir" remote set-url origin "$origin_url"
    git -C "$repo_dir" fetch origin
  else
    if [[ -d "$repo_dir" ]] && [[ -n "$(ls -A "$repo_dir")" ]]; then
      echo "Error: Directory '$repo_dir' exists and is not empty, but is not a git repository." >&2
      exit 1
    fi
    git clone --origin origin "$origin_url" "$repo_dir"
  fi

  git -C "$repo_dir" checkout "$branch"
  git -C "$repo_dir" pull --rebase origin "$branch"
}

ubuntu_launch_run_install() {
  local repo_dir="$1"
  shift
  local install_script="$repo_dir/install.sh"
  if [[ ! -x "$install_script" ]]; then
    echo "install.sh not found or not executable at $install_script" >&2
    exit 1
  fi

  echo "Running installer: $install_script $*"
  "$install_script" "$@"
}

ubuntu_launch_main() {
  local script_name="$1"
  local expected_version="$2"
  shift 2

  local repo_dir="${ARTHEXIS_REPO_DIR:-$DEFAULT_REPO_DIR}"
  local origin_url="${ARTHEXIS_ORIGIN_URL:-$DEFAULT_ORIGIN_URL}"
  local branch="${ARTHEXIS_BRANCH:-$DEFAULT_BRANCH}"
  local -a install_args=()

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --repo-dir)
        repo_dir="$2"
        shift 2
        ;;
      --origin)
        origin_url="$2"
        shift 2
        ;;
      --branch)
        branch="$2"
        shift 2
        ;;
      -h|--help)
        ubuntu_launch_usage "$script_name"
        exit 0
        ;;
      --)
        shift
        install_args+=("$@")
        break
        ;;
      *)
        echo "Unknown option: $1" >&2
        ubuntu_launch_usage "$script_name"
        exit 1
        ;;
    esac
  done

  ubuntu_launch_warn_if_not_version "$expected_version"
  ubuntu_launch_install_packages
  ubuntu_launch_prepare_repo "$repo_dir" "$origin_url" "$branch"
  ubuntu_launch_run_install "$repo_dir" "${install_args[@]}"
}
