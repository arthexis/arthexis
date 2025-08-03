#!/usr/bin/env bash
set -e

VENV_DIR=".venv"
if [ ! -d "$VENV_DIR" ]; then
  python -m venv "$VENV_DIR"
fi

PYTHON="$VENV_DIR/bin/python"
if [ ! -f "$PYTHON" ]; then
  echo "Virtual environment not found" >&2
  exit 0
fi

if [ -f requirements.txt ]; then
  "$PYTHON" -m pip install -r requirements.txt
fi

"$PYTHON" dev_maintenance.py
