#!/usr/bin/env bash

repo_root=$(git rev-parse --show-toplevel 2>/dev/null)
if [ -z "$repo_root" ]; then
  exit 0
fi

# shellcheck source=scripts/helpers/version_marker.sh
. "$repo_root/scripts/helpers/version_marker.sh"

arthexis_prepare_dev_version_marker "$repo_root"
