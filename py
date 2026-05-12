#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"

for venv_dir in .venv venv; do
  for python_path in "$venv_dir/bin/python" "$venv_dir/Scripts/python.exe"; do
    if [[ -x "$BASE_DIR/$python_path" ]]; then
      exec "$BASE_DIR/$python_path" "$@"
    fi
  done
done

cat >&2 <<'MSG'
No project virtual environment Python was found.

Expected one of:
  .venv/bin/python
  .venv/Scripts/python.exe
  venv/bin/python
  venv/Scripts/python.exe

Bootstrap the environment first:
  ./install.sh

Then rerun your command, for example:
  ./py manage.py test run -- apps/sites
MSG
exit 1
