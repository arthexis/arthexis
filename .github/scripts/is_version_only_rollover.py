#!/usr/bin/env python3
import re
import subprocess
import sys

before, after = sys.argv[1:3]
changed = subprocess.check_output(
    ["git", "diff", "--name-only", before, after],
    text=True,
).splitlines()

if set(changed) != {"VERSION", "pyproject.toml"}:
    raise SystemExit(1)

old_version = subprocess.check_output(
    ["git", "show", f"{before}:VERSION"], text=True
).strip()
new_version = subprocess.check_output(
    ["git", "show", f"{after}:VERSION"], text=True
).strip()
old_pyproject = subprocess.check_output(
    ["git", "show", f"{before}:pyproject.toml"], text=True
)
new_pyproject = subprocess.check_output(
    ["git", "show", f"{after}:pyproject.toml"], text=True
)

pattern = re.compile(r'(?m)^(version\s*=\s*")[^"]+(".*)$')
old_match = pattern.search(old_pyproject)
new_match = pattern.search(new_pyproject)
if not (old_match and new_match):
    raise SystemExit(1)

old_normalized = pattern.sub(r'\1<VERSION>\2', old_pyproject, count=1)
new_normalized = pattern.sub(r'\1<VERSION>\2', new_pyproject, count=1)
pyproject_version = re.search(
    r'(?m)^version\s*=\s*"([^"]+)"',
    new_pyproject,
).group(1)

if (
    old_version != new_version
    and pyproject_version == new_version
    and old_pyproject != new_pyproject
    and old_normalized == new_normalized
):
    raise SystemExit(0)

raise SystemExit(1)
