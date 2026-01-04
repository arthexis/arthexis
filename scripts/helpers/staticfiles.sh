#!/usr/bin/env bash

arthexis_staticfiles_snapshot_check() {
  local md5_file="$1"
  local meta_file="$2"

  if [ ! -f "$md5_file" ] || [ ! -f "$meta_file" ]; then
    return 4
  fi

  python - "$md5_file" "$meta_file" <<'PY'
import json
import sys
from pathlib import Path

def filesystem_snapshot(roots):
    latest_mtime = 0
    file_count = 0
    saw_root = False
    for raw_root in roots:
        root = Path(raw_root)
        if not root.exists():
            continue
        saw_root = True
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            latest_mtime = max(latest_mtime, stat.st_mtime_ns)
            file_count += 1
    if not saw_root:
        return None, file_count
    return (latest_mtime if file_count else 0), file_count

md5_path = Path(sys.argv[1])
meta_path = Path(sys.argv[2])

try:
    stored_hash = md5_path.read_text().strip()
except OSError:
    sys.exit(4)

try:
    metadata = json.loads(meta_path.read_text())
except (OSError, json.JSONDecodeError):
    sys.exit(4)

latest_mtime = metadata.get("latest_mtime_ns")
file_count = metadata.get("file_count")
roots = metadata.get("roots") or []

if latest_mtime is None or file_count is None:
    sys.exit(3)

current_mtime, current_count = filesystem_snapshot(roots)
if current_mtime is None:
    sys.exit(3)

if current_mtime == latest_mtime and current_count == file_count:
    print(stored_hash)
    sys.exit(0)

sys.exit(3)
PY
}

arthexis_staticfiles_compute_hash() {
  local md5_file="$1"
  local meta_file="$2"
  local force_collectstatic="$3"
  local hash_script="${STATICFILES_HASH_SCRIPT:-scripts/staticfiles_md5.py}"
  local hash_args=()
  local metadata_tmp="${meta_file}.tmp"

  if [ "$force_collectstatic" = true ]; then
    hash_args+=(--ignore-cache)
  fi

  if ! hash_output=$(python "$hash_script" --metadata-output "$metadata_tmp" "${hash_args[@]}"); then
    rm -f "$metadata_tmp"
    return 2
  fi

  if [ ! -f "$metadata_tmp" ]; then
    return 3
  fi

  mv "$metadata_tmp" "$meta_file"
  echo "$hash_output" > "$md5_file"
  printf '%s' "$hash_output"
  return 0
}

arthexis_prepare_staticfiles_hash() {
  local md5_file="$1"
  local meta_file="$2"
  local force_collectstatic="$3"
  local hash_value=""

  if [ "$force_collectstatic" = false ]; then
    set +e
    hash_value=$(arthexis_staticfiles_snapshot_check "$md5_file" "$meta_file")
    status=$?
    set -e

    if [ "$status" -eq 0 ] && [ -n "$hash_value" ]; then
      ARTHEXIS_STATICFILES_FAST_PATH_USED=true
      printf '%s' "$hash_value"
      return 0
    elif [ "$status" -ne 3 ]; then
      echo "Static files metadata unavailable (exit $status); recalculating." >&2
    fi
  fi

  set +e
  hash_value=$(arthexis_staticfiles_compute_hash "$md5_file" "$meta_file" "$force_collectstatic")
  status=$?
  set -e

  if [ "$status" -ne 0 ]; then
    return $status
  fi

  ARTHEXIS_STATICFILES_FAST_PATH_USED=false
  printf '%s' "$hash_value"
  return 0
}
