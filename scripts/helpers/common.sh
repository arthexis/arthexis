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
  local candidate

  if [ -n "${_arthexis_python_bin_cached-}" ]; then
    if [ "$_arthexis_python_bin_cached" = "not_found" ]; then
      return 1
    fi
    printf '%s' "$_arthexis_python_bin_cached"
    return 0
  fi

  if command -v python3 >/dev/null 2>&1; then
    _arthexis_python_bin_cached="python3"
  elif command -v python >/dev/null 2>&1; then
    if python -c 'import sys; raise SystemExit(0 if sys.version_info[0] == 3 else 1)' >/dev/null 2>&1; then
      _arthexis_python_bin_cached="python"
    else
      _arthexis_python_bin_cached="not_found"
      return 1
    fi
  else
    while IFS= read -r candidate; do
      case "$candidate" in
        python3|python3[0-9]|python3.[0-9]*)
          if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info[0] == 3 else 1)' >/dev/null 2>&1; then
            _arthexis_python_bin_cached="$candidate"
            break
          fi
          ;;
      esac
    done < <(compgen -c python3 | sort -rV -u)

    if [ -z "${_arthexis_python_bin_cached-}" ]; then
      _arthexis_python_bin_cached="not_found"
      return 1
    fi
  fi

  printf '%s' "$_arthexis_python_bin_cached"
}
