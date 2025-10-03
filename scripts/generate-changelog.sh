#!/usr/bin/env bash
set -euo pipefail

# Generate CHANGELOG.rst from commit messages.
# Usage: scripts/generate-changelog.sh [starting-tag]
# If starting-tag is omitted, the last tag is used.

start_tag="${1:-$(git describe --tags --abbrev=0 2>/dev/null || echo '')}"

if [ -n "$start_tag" ]; then
  range="$start_tag..HEAD"
else
  range="HEAD"
fi

python3 - "$range" <<'PY'
import sys
from pathlib import Path

from core import changelog


range_spec = sys.argv[1] if len(sys.argv) > 1 else "HEAD"
path = Path("CHANGELOG.rst")
previous = path.read_text(encoding="utf-8") if path.exists() else None
sections = changelog.collect_sections(range_spec=range_spec, previous_text=previous)
content = changelog.render_changelog(sections)
if not content.endswith("\n"):
    content += "\n"
path.write_text(content, encoding="utf-8")
PY
