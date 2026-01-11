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
