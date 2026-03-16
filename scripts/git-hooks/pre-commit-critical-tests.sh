#!/usr/bin/env bash
set -euo pipefail

# Ensure we are in a virtual environment to have access to all dependencies.
if [ -z "${VIRTUAL_ENV:-}" ]; then
    echo "Error: This hook requires an active virtual environment. Please activate it and try again." >&2
    exit 1
fi

echo "Running critical and regression tests..."
pytest -m "critical or regression"
