#!/usr/bin/env bash
set -euo pipefail

DEFAULT_DEPLOY_PATH="/home/arthe/canary"
DEPLOY_PATH="${CANARY_DEPLOY_PATH:-$DEFAULT_DEPLOY_PATH}"
TARGET_REF="${CANARY_DEPLOY_REF:-${GITHUB_SHA:-main}}"
DRY_RUN=0

usage() {
  cat <<'EOF'
Usage: scripts/github-canary-deploy.sh [options]

Options:
  --deploy-path PATH   Absolute path to the deployed checkout.
  --ref REF            Branch, tag, or commit SHA to deploy.
  --dry-run            Validate and print plan without changing files/services.
  --help               Show this message.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --deploy-path)
      DEPLOY_PATH="${2:-}"
      shift 2
      ;;
    --ref)
      TARGET_REF="${2:-}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
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

if [[ -z "${DEPLOY_PATH}" ]]; then
  echo "Deploy path cannot be empty." >&2
  exit 2
fi

if [[ -z "${TARGET_REF}" ]]; then
  echo "Target ref cannot be empty." >&2
  exit 2
fi

if [[ ! -d "${DEPLOY_PATH}" ]]; then
  echo "Deploy path does not exist: ${DEPLOY_PATH}" >&2
  exit 1
fi

if [[ ! -d "${DEPLOY_PATH}/.git" ]]; then
  echo "Deploy path is not a git checkout: ${DEPLOY_PATH}" >&2
  exit 1
fi

cd "${DEPLOY_PATH}"

if ! git remote get-url origin >/dev/null 2>&1; then
  echo "Deploy checkout has no 'origin' remote configured." >&2
  exit 1
fi

if ! git diff --quiet --ignore-submodules -- || ! git diff --cached --quiet --ignore-submodules --; then
  echo "Refusing to deploy with tracked local changes in ${DEPLOY_PATH}." >&2
  echo "Commit/stash/revert local changes first." >&2
  exit 1
fi

echo "Fetching latest refs from origin..."
git fetch --prune --tags origin

resolve_commit() {
  local ref="$1"
  if git rev-parse --verify --quiet "${ref}^{commit}" >/dev/null; then
    git rev-parse "${ref}^{commit}"
    return 0
  fi
  if git rev-parse --verify --quiet "origin/${ref}^{commit}" >/dev/null; then
    git rev-parse "origin/${ref}^{commit}"
    return 0
  fi
  return 1
}

if ! RESOLVED_COMMIT="$(resolve_commit "${TARGET_REF}")"; then
  echo "Unable to resolve target ref '${TARGET_REF}' in ${DEPLOY_PATH}." >&2
  exit 1
fi

CURRENT_COMMIT="$(git rev-parse HEAD)"

echo "Deploy path: ${DEPLOY_PATH}"
echo "Current commit: ${CURRENT_COMMIT}"
echo "Target ref: ${TARGET_REF}"
echo "Resolved commit: ${RESOLVED_COMMIT}"

if [[ "${DRY_RUN}" -eq 1 ]]; then
  echo "Dry run requested; skipping checkout and upgrade."
  exit 0
fi

if [[ "${CURRENT_COMMIT}" != "${RESOLVED_COMMIT}" ]]; then
  echo "Checking out target commit..."
  git checkout --detach "${RESOLVED_COMMIT}"
else
  echo "Deploy checkout is already at target commit."
fi

if [[ ! -x "./upgrade.sh" ]]; then
  echo "upgrade.sh is missing or not executable in ${DEPLOY_PATH}." >&2
  exit 1
fi

echo "Running local upgrade and service restart..."
./upgrade.sh --local --start --no-warn

if [[ -x "./status.sh" ]]; then
  echo "Service status after deployment:"
  ./status.sh || true
fi

echo "Canary deployment complete at ${RESOLVED_COMMIT}."
