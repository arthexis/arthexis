#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 <archive-path>" >&2
  exit 1
}

if [[ $# -ne 1 ]]; then
  usage
fi

archive="$1"
if [[ "$archive" != *.tgz && "$archive" != *.tar.gz ]]; then
  archive="${archive}.tgz"
fi
archive="$(realpath -m "$archive")"

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

files="$(git ls-files --others --ignored --exclude-standard)"
if [[ -z "$files" ]]; then
  echo "No files to backup. Creating empty archive."
  tar -czf "$archive" --files-from /dev/null
else
  git ls-files --others --ignored --exclude-standard -z \
    | tar -czf "$archive" --null -T -
fi

echo "Backup created at $archive"
