#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ -x "$BASE_DIR/.venv/bin/python" ]]; then
  exec "$BASE_DIR/.venv/bin/python" "$@"
fi

if [[ -x "$BASE_DIR/venv/bin/python" ]]; then
  exec "$BASE_DIR/venv/bin/python" "$@"
fi

cat >&2 <<'MSG'
No project virtual environment Python was found.

Expected one of:
  .venv/bin/python
  venv/bin/python

Bootstrap the environment first:
  ./install.sh

Then rerun your command, for example:
  ./py manage.py test run -- apps/sites
MSG
exit 1
