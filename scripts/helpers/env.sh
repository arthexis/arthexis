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
import shlex
import sys

path = sys.argv[1]

def parse_env(line):
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None, None
    if stripped.startswith("export "):
        stripped = stripped[7:].lstrip()
    if "=" not in stripped:
        return None, None
    key, value = stripped.split("=", 1)
    key = key.strip()
    if not key:
        return None, None
    value = value.strip()
    if value and value[0] in ("'", '"'):
        quote = value[0]
        if value.endswith(quote):
            value = value[1:-1]
        else:
            value = value[1:]
        if quote == '"':
            value = bytes(value, "utf-8").decode("unicode_escape")
    else:
        if "#" in value:
            value = value.split("#", 1)[0].rstrip()
    return key, value

with open(path, "r", encoding="utf-8") as handle:
    for raw in handle:
        key, value = parse_env(raw)
        if key is None or value is None:
            continue
        print(f"export {key}={shlex.quote(value)}")
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
