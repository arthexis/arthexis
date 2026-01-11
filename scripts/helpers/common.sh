# shellcheck shell=bash

normalize_path() {
  local raw="$1"
  local converted=""

  if [ -z "$raw" ]; then
    return 1
  fi

  if command -v wslpath >/dev/null 2>&1; then
    case "$raw" in
      [A-Za-z]:\\*|[A-Za-z]:/*)
        converted=$(wslpath -u "$raw" 2>/dev/null) || converted=""
        if [ -n "$converted" ]; then
          printf '%s' "$converted"
          return 0
        fi
        ;;
    esac
  fi

  printf '%s' "$raw"
}

arthexis_python_bin() {
  local python_bin=""

  if command -v python3 >/dev/null 2>&1; then
    python_bin="python3"
  elif command -v python >/dev/null 2>&1; then
    python_bin="python"
  fi

  if [ -z "$python_bin" ]; then
    return 1
  fi

  printf '%s' "$python_bin"
}
