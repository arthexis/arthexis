#!/usr/bin/env bash
set -euo pipefail

SCRIPT_NAME="$(basename "$0")"
DEFAULT_REPO_DIR="/opt/arthexis"
REPO_DIR="${ARTHEXIS_REPO_DIR:-$DEFAULT_REPO_DIR}"
ORIGIN_URL="${ARTHEXIS_ORIGIN_URL:-https://github.com/arthexis/arthexis.git}"
BRANCH="${ARTHEXIS_BRANCH:-main}"
INSTALL_ARGS=()

usage() {
  cat <<USAGE
Usage: $SCRIPT_NAME [options] [-- install.sh args]

Options:
  --repo-dir PATH     Target repo directory (default: $DEFAULT_REPO_DIR)
  --origin URL        Git origin URL (default: $ORIGIN_URL)
  --branch NAME       Git branch to checkout (default: $BRANCH)
  -h, --help          Show this help output

Examples:
  $SCRIPT_NAME --repo-dir /opt/arthexis -- --terminal --start
  $SCRIPT_NAME --branch main -- --control --service arthexis --start
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-dir)
      REPO_DIR="$2"
      shift 2
      ;;
    --origin)
      ORIGIN_URL="$2"
      shift 2
      ;;
    --branch)
      BRANCH="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      INSTALL_ARGS+=("$@")
      break
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

warn_if_not_ubuntu_22() {
  if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    if [[ "${ID:-}" != "ubuntu" || "${VERSION_ID:-}" != 22.04* ]]; then
      echo "Warning: This script targets Ubuntu 22.04. Detected ${PRETTY_NAME:-unknown}."
    fi
  fi
}

ensure_sudo() {
  if [[ "$EUID" -ne 0 ]]; then
    if ! command -v sudo >/dev/null 2>&1; then
      echo "sudo is required to install system dependencies." >&2
      exit 1
    fi
    echo "sudo"  # shellcheck disable=SC2124
  fi
}

install_packages() {
  local sudo_cmd
  sudo_cmd=$(ensure_sudo)

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

prepare_repo() {
  if [[ -d "$REPO_DIR/.git" ]]; then
    git -C "$REPO_DIR" remote set-url origin "$ORIGIN_URL"
    git -C "$REPO_DIR" fetch origin
  else
    git clone --origin origin "$ORIGIN_URL" "$REPO_DIR"
  fi

  git -C "$REPO_DIR" checkout "$BRANCH"
  git -C "$REPO_DIR" pull --rebase origin "$BRANCH"
}

run_install() {
  local install_script="$REPO_DIR/install.sh"
  if [[ ! -x "$install_script" ]]; then
    echo "install.sh not found or not executable at $install_script" >&2
    exit 1
  fi

  echo "Running installer: $install_script ${INSTALL_ARGS[*]}"
  "$install_script" "${INSTALL_ARGS[@]}"
}

warn_if_not_ubuntu_22
install_packages
prepare_repo
run_install
