#!/usr/bin/env bash

# Normalize the VERSION file and remove historical development markers.
#
# Arguments:
#   $1 - Repository root containing the VERSION file
#   $2 - Deprecated (ignored). Kept for call-site compatibility.
#
# Arthexis versions are stored exactly as declared in ``VERSION``. We no longer
# append development suffixes such as ``+d`` or legacy ``+`` markers. Existing
# marker suffixes are stripped for backward compatibility.
arthexis_update_version_marker() {
  local repo_root="$1"
  local version_file
  version_file="$repo_root/VERSION"

  if [ ! -f "$version_file" ]; then
    return 0
  fi

  local raw_version
  raw_version=$(tr -d '\r\n' < "$version_file")
  if [ -z "$raw_version" ]; then
    return 0
  fi

  local normalized_version="$raw_version"
  normalized_version="${normalized_version%+d}"
  normalized_version="${normalized_version%+}"

  # Ensure the file always ends with a newline.
  printf '%s\n' "$normalized_version" > "$version_file"
}

# No-op retained for backward compatibility with older hooks/workflows.
#
# Arguments:
#   $1 - Repository root containing the VERSION file
arthexis_prepare_dev_version_marker() {
  return 0
}
