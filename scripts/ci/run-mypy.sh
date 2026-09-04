#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$REPO_ROOT"

if [[ ! -x .venv/bin/python ]]; then
  echo "Expected .venv/bin/python; run the install/environment setup first." >&2
  exit 1
fi

exec .venv/bin/python -m mypy \
  --python-version 3.11 \
  --check-untyped-defs \
  --warn-redundant-casts \
  --warn-unused-ignores \
  --no-implicit-optional \
  --follow-imports=silent \
  --show-error-codes \
  apps/ocpp/payload_types.py \
  apps/ocpp/consumers/csms/protocol.py \
  apps/ocpp/consumers/base/dispatch.py \
  apps/ocpp/auto_start.py \
  apps/ocpp/store/__init__.py
