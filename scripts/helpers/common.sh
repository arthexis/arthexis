# shellcheck shell=bash

arthexis_find_python() {
  local candidate
  local candidates=("${ARTHEXIS_PYTHON_BIN:-}" python python3 py python.exe)

  for candidate in "${candidates[@]}"; do
    if [ -n "$candidate" ] && command -v "$candidate" >/dev/null 2>&1; then
      printf '%s' "$candidate"
      return 0
    fi
  done

  return 1
}
