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
  local -a ordered_candidates=()

  if candidate="$(arthexis_python_bin 2>/dev/null)"; then
    ordered_candidates+=("$candidate")
  fi

  if command -v python3 >/dev/null 2>&1; then
    ordered_candidates+=("python3")
  fi

  while IFS= read -r candidate; do
    [ -n "$candidate" ] || continue
    ordered_candidates+=("$candidate")
  done < <(compgen -c python3 | sort -u)

  for candidate in "${ordered_candidates[@]}"; do
    resolved="$(command -v "$candidate" 2>/dev/null || true)"
    [ -n "$resolved" ] || continue

    if "$resolved" -c 'import sys, venv; raise SystemExit(0 if sys.version_info[0] == 3 else 1)' >/dev/null 2>&1; then
      printf '%s' "$resolved"
      return 0
    fi
  done

  return 1
}

