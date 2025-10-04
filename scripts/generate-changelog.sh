#!/usr/bin/env bash
set -euo pipefail

# Generate CHANGELOG.rst from commit messages.
# Usage: scripts/generate-changelog.sh [starting-tag]
# If starting-tag is omitted, the last release tag is used.

start_tag="${1:-}"

python3 - "$start_tag" <<'PY'
import sys
from pathlib import Path

from core import changelog


start_tag = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else None
range_spec = changelog.determine_range_spec(start_tag)
path = Path("CHANGELOG.rst")
previous = path.read_text(encoding="utf-8") if path.exists() else None
sections = changelog.collect_sections(range_spec=range_spec, previous_text=previous)
content = changelog.render_changelog(sections)
if not content.endswith("\n"):
    content += "\n"
path.write_text(content, encoding="utf-8")
PY
