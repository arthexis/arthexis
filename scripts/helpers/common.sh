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
  local path_entry
  local candidate
  local versioned_candidates
  local current_path

  _arthexis_python_candidate_is_py3() {
    local python_candidate="$1"
    "$python_candidate" -c 'import sys; raise SystemExit(0 if sys.version_info[0] == 3 else 1)' >/dev/null 2>&1
  }

  _arthexis_python_path_entry_for_name() {
    local entry="$1"
    local name="$2"

    for candidate in "$entry/$name" "$entry/$name.exe" "$entry/$name.bat" "$entry/$name.cmd"; do
      if [ -f "$candidate" ] && [ -x "$candidate" ]; then
        printf '%s' "$candidate"
        return 0
      fi
    done

    return 1
  }

  # Cache is PATH-dependent so inherited shell state cannot override lookup
  # results when the caller provides a different PATH.
  current_path="${PATH-}"
  if [ "${_arthexis_python_bin_cached_path+x}" = "x" ] && [ "$_arthexis_python_bin_cached_path" = "$current_path" ] && [ -n "${_arthexis_python_bin_cached-}" ]; then
    if [ "$_arthexis_python_bin_cached" = "not_found" ]; then
      return 1
    fi
    printf '%s' "$_arthexis_python_bin_cached"
    return 0
  fi

  unset _arthexis_python_bin_cached
  _arthexis_python_bin_cached_path="$current_path"

  local _arthexis_path_remainder
  _arthexis_path_remainder="${PATH}:"

  while [ -n "$_arthexis_path_remainder" ]; do
    path_entry=${_arthexis_path_remainder%%:*}
    _arthexis_path_remainder=${_arthexis_path_remainder#*:}

    if [ -z "$path_entry" ]; then
      path_entry='.'
    fi

    candidate=$(_arthexis_python_path_entry_for_name "$path_entry" "python3")
    if [ -n "$candidate" ] && _arthexis_python_candidate_is_py3 "$candidate"; then
      _arthexis_python_bin_cached="$candidate"
      break
    fi

    candidate=$(_arthexis_python_path_entry_for_name "$path_entry" "python")
    if [ -n "$candidate" ] && _arthexis_python_candidate_is_py3 "$candidate"; then
      _arthexis_python_bin_cached="$candidate"
      break
    fi

    versioned_candidates=""
    while IFS= read -r candidate; do
      if [ -z "$candidate" ]; then
        continue
      fi
      if [ -n "$versioned_candidates" ]; then
        versioned_candidates+=$'\n'
      fi
      versioned_candidates+="$candidate"
    done < <(
      compgen -f "$path_entry/python3" | while IFS= read -r resolved; do
        candidate=${resolved##*/}
        case "$candidate" in
          python3|python3[0-9]|python3.[0-9]*)
            printf '%s\n' "$resolved"
            ;;
          python3.exe|python3.bat|python3.cmd|python3[0-9].exe|python3[0-9].bat|python3[0-9].cmd|python3.[0-9]*.exe|python3.[0-9]*.bat|python3.[0-9]*.cmd)
            printf '%s\n' "$resolved"
            ;;
        esac
      done | sort -rV -u
    )

    while IFS= read -r candidate; do
      if [ -z "$candidate" ]; then
        continue
      fi
      if _arthexis_python_candidate_is_py3 "$candidate"; then
        _arthexis_python_bin_cached="$candidate"
        break 2
      fi
    done <<<"$versioned_candidates"
  done

  if [ -z "${_arthexis_python_bin_cached-}" ]; then
    _arthexis_python_bin_cached="not_found"
    return 1
  fi

  printf '%s' "$_arthexis_python_bin_cached"
}
