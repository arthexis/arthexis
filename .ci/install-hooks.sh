#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

chmod +x .ci/hooks/pre-commit .ci/quality.sh
git config core.hooksPath .ci/hooks

echo "Installed repository hooks from .ci/hooks"
