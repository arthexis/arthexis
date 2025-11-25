#!/usr/bin/env bash

# Update the VERSION file to include or remove the development marker.
#
# Arguments:
#   $1 - Repository root containing the VERSION file
#
# The function compares the current HEAD revision to the tagged revision for the
# VERSION's release (without the development marker). When the HEAD revision
# differs from the release tag, a ``+d`` suffix is appended to the VERSION file
# to indicate development changes. When the HEAD revision matches the tag, any
# trailing development marker is removed so that packaged releases retain their
# original version number. Legacy versions ending with ``+`` are handled for
# backward compatibility.
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

  local dev_suffix="+d"
  local legacy_suffix="+"
  local has_dev_marker=0

  local base_version="$raw_version"
  if [[ "$raw_version" == *"$dev_suffix" ]]; then
    has_dev_marker=1
    base_version="${raw_version%$dev_suffix}"
  elif [[ "$raw_version" == *"$legacy_suffix" ]]; then
    has_dev_marker=1
    base_version="${raw_version%$legacy_suffix}"
  fi
  if [ -z "$base_version" ]; then
    return 0
  fi

  local head_rev
  if ! head_rev=$(git -C "$repo_root" rev-parse HEAD 2>/dev/null); then
    return 0
  fi

  local tag_ref="v${base_version}"
  local tag_rev
  if git -C "$repo_root" rev-parse --verify --quiet "${tag_ref}^{commit}" >/dev/null 2>&1; then
    tag_rev=$(git -C "$repo_root" rev-parse "${tag_ref}^{commit}" 2>/dev/null)
  else
    tag_rev=""
  fi

  if [ -n "$tag_rev" ] && [ "$head_rev" != "$tag_rev" ]; then
    if (( ! has_dev_marker )); then
      printf '%s%s\n' "$base_version" "$dev_suffix" > "$version_file"
    fi
  elif (( has_dev_marker )); then
    printf '%s\n' "$base_version" > "$version_file"
  else
    # Ensure the file always ends with a newline.
    printf '%s\n' "$raw_version" > "$version_file"
  fi
}
