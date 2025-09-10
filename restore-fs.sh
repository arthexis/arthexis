#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 <archive-path>" >&2
}

if [[ $# -ne 1 ]]; then
  usage
  exit 1
fi

if [[ "$1" == "-h" || "$1" == "--help" ]]; then
  usage
  exit 0
fi

archive="$(realpath -m "$1")"
if [[ ! -f "$archive" ]]; then
  echo "Archive not found: $archive" >&2
  exit 1
fi

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

tar -xzf "$archive"

echo "Restore complete from $archive"
