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
    _arthexis_python_bin_cached="not_found"
    return 1
  fi

  printf '%s' "$_arthexis_python_bin_cached"
}

arthexis_python_venv_creator() {
  local candidate
  local resolved
  local -a ordered_candidates=(python3 python3.13 python3.12 python3.11 python3.10 python3.9 python3.8)
  local -A seen_resolved=()

  if candidate="$(arthexis_python_bin 2>/dev/null)"; then
    ordered_candidates=("$candidate" "${ordered_candidates[@]}")
  fi

  for candidate in "${ordered_candidates[@]}"; do
    resolved="$(command -v "$candidate" 2>/dev/null || true)"
    [ -n "$resolved" ] || continue
    if [ -n "${seen_resolved["$resolved"]+x}" ]; then
      continue
    fi
    seen_resolved["$resolved"]=1

    if "$resolved" -c 'import sys, venv; raise SystemExit(0 if sys.version_info[0] == 3 else 1)' >/dev/null 2>&1; then
      printf '%s' "$resolved"
      return 0
    fi
  done

  return 1
}
