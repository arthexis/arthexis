#!/usr/bin/env bash
# shellcheck shell=bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/helpers/common.sh
. "$SCRIPT_DIR/common.sh"

arthexis_load_env_file() {
  local base_dir="$1"
  local env_file="${2:-$base_dir/arthexis.env}"

  if [ -z "$env_file" ] || [ ! -f "$env_file" ]; then
    return 0
  fi

  if [ "${ARTHEXIS_ENV_LOADED:-}" = "1" ] && [ "${ARTHEXIS_ENV_FILE:-}" = "$env_file" ]; then
    return 0
  fi

  local python_bin
  python_bin="$(arthexis_python_bin 2>/dev/null || true)"
  if [ -n "$python_bin" ]; then
    local export_lines
    if export_lines="$(
      "$python_bin" - "$env_file" <<'PY'
import re
import shlex
import sys

from dotenv import dotenv_values

path = sys.argv[1]
valid_key = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

for key, value in dotenv_values(path).items():
    if key is None or value is None:
        continue
    if not valid_key.match(key):
        continue
    value = value.replace("\\$", "$").replace("\\`", "`").replace("\\!", "!")
    print(f"export {shlex.quote(key)}={shlex.quote(value)}")
PY
    )"; then
      if [ -n "$export_lines" ]; then
        eval "$export_lines"
      fi
      export ARTHEXIS_ENV_LOADED="1"
      export ARTHEXIS_ENV_FILE="$env_file"
      return 0
    fi
  fi

  set -a
  # shellcheck disable=SC1090
  . "$env_file"
  set +a
  export ARTHEXIS_ENV_LOADED="1"
  export ARTHEXIS_ENV_FILE="$env_file"
}
