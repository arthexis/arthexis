#!/usr/bin/env bash
set -euo pipefail

RUNNER_VERSION="${RUNNER_VERSION:-2.327.1}"
RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner-canary}"
RUNNER_NAME="${RUNNER_NAME:-$(hostname)-canary}"
RUNNER_LABELS="${RUNNER_LABELS:-canary,linux}"
RUNNER_WORKDIR="${RUNNER_WORKDIR:-_work}"
RUNNER_URL="${RUNNER_URL:-}"
RUNNER_TOKEN="${RUNNER_TOKEN:-}"
RUNNER_USER="${RUNNER_USER:-$(id -un)}"
INSTALL_SERVICE=1
REPLACE_EXISTING=1

usage() {
  cat <<'EOF'
Usage: scripts/setup-github-runner-canary.sh [options]

Required:
  --url URL            GitHub repo or org URL, e.g. https://github.com/owner/repo
  --token TOKEN        One-time runner registration token

Optional:
  --dir PATH           Runner install directory (default: ~/actions-runner-canary)
  --name NAME          Runner name (default: <hostname>-canary)
  --labels LIST        Comma-separated labels (default: canary,linux)
  --workdir PATH       Runner work directory (default: _work)
  --version VER        Runner version (default: 2.327.1)
  --user USER          Service user for svc.sh install (default: current user)
  --no-service         Configure runner but do not install/start system service
  --no-replace         Do not use --replace during runner config
  --help               Show this message

Environment variables with the same names are also supported.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url)
      RUNNER_URL="${2:-}"
      shift 2
      ;;
    --token)
      RUNNER_TOKEN="${2:-}"
      shift 2
      ;;
    --dir)
      RUNNER_DIR="${2:-}"
      shift 2
      ;;
    --name)
      RUNNER_NAME="${2:-}"
      shift 2
      ;;
    --labels)
      RUNNER_LABELS="${2:-}"
      shift 2
      ;;
    --workdir)
      RUNNER_WORKDIR="${2:-}"
      shift 2
      ;;
    --version)
      RUNNER_VERSION="${2:-}"
      shift 2
      ;;
    --user)
      RUNNER_USER="${2:-}"
      shift 2
      ;;
    --no-service)
      INSTALL_SERVICE=0
      shift
      ;;
    --no-replace)
      REPLACE_EXISTING=0
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "${RUNNER_URL}" ]]; then
  echo "Missing --url (or RUNNER_URL)." >&2
  exit 2
fi

if [[ -z "${RUNNER_TOKEN}" ]]; then
  echo "Missing --token (or RUNNER_TOKEN)." >&2
  exit 2
fi

arch="$(uname -m)"
case "${arch}" in
  x86_64|amd64)
    runner_arch="x64"
    ;;
  aarch64|arm64)
    runner_arch="arm64"
    ;;
  armv7l|armv6l)
    runner_arch="arm"
    ;;
  *)
    echo "Unsupported architecture: ${arch}" >&2
    exit 1
    ;;
esac

mkdir -p "${RUNNER_DIR}"
cd "${RUNNER_DIR}"

if [[ ! -x "./config.sh" ]]; then
  tarball="actions-runner-linux-${runner_arch}-${RUNNER_VERSION}.tar.gz"
  download_url="https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${tarball}"
  echo "Downloading GitHub runner ${RUNNER_VERSION} (${runner_arch})..."
  curl -fsSL -o "${tarball}" "${download_url}"
  tar xzf "${tarball}"
  rm -f "${tarball}"
fi

config_args=(
  --url "${RUNNER_URL}"
  --token "${RUNNER_TOKEN}"
  --name "${RUNNER_NAME}"
  --labels "${RUNNER_LABELS}"
  --work "${RUNNER_WORKDIR}"
  --unattended
)
if [[ "${REPLACE_EXISTING}" -eq 1 ]]; then
  config_args+=(--replace)
fi

echo "Configuring runner '${RUNNER_NAME}' in ${RUNNER_DIR}..."
./config.sh "${config_args[@]}"

if [[ "${INSTALL_SERVICE}" -eq 0 ]]; then
  echo "Runner configured without system service. Start manually with: ${RUNNER_DIR}/run.sh"
  exit 0
fi

if [[ ! -x "./svc.sh" ]]; then
  echo "svc.sh not found; cannot install service." >&2
  exit 1
fi

sudo_cmd=()
if command -v sudo >/dev/null 2>&1; then
  sudo_cmd=(sudo)
fi

echo "Installing runner service for user ${RUNNER_USER}..."
"${sudo_cmd[@]}" ./svc.sh install "${RUNNER_USER}"
"${sudo_cmd[@]}" ./svc.sh start

echo "Runner service installed and started."
