#!/usr/bin/env python3
"""Find duplicate or superseded dependency bump PR candidates."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from typing import Any

FIELDS = "number,title,author,headRefName,baseRefName,url,updatedAt"


def gh_json(args: list[str]) -> Any:
    if not shutil.which("gh"):
        raise SystemExit("gh CLI not found on PATH")
    proc = subprocess.run(["gh", *args], text=True, capture_output=True, check=True)
    return json.loads(proc.stdout or "null")


def author_login(pr: dict[str, Any]) -> str:
    author = pr.get("author") or {}
    return str(author.get("login") if isinstance(author, dict) else author)


def compact(value: str) -> str:
    return " ".join(value.strip().lower().split())


def strip_dependabot_ecosystem(value: str) -> str:
    parts = value.split("/", 2)
    if len(parts) == 3 and parts[0] == "dependabot":
        return parts[2]
    return value


def starts_with_version(value: str) -> bool:
    normalized = value.lower().removeprefix("v")
    version_chars = []
    for char in normalized:
        if char.isdigit() or char == ".":
            version_chars.append(char)
            continue
        break
    version = "".join(version_chars)
    return "." in version and all(part.isdigit() for part in version.split(".") if part)


def strip_version_suffix(value: str) -> str:
    for separator in ("-", "_"):
        parts = value.split(separator)
        for index in range(len(parts) - 1, 0, -1):
            if starts_with_version(separator.join(parts[index:])):
                return separator.join(parts[:index])
    return value


def package_key(pr: dict[str, Any]) -> str:
    title = str(pr.get("title") or "")
    patterns = [
        r"\bbump\s+(.+?)\s+from\s+",
        r"\bbump\s+(.+?)\s+in\s+",
        r"\bupdate\s+dependency\s+(.+?)(?:\s|$)",
        r"\bdeps\):\s*(.+?)(?:\s|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, title, re.IGNORECASE)
        if match:
            return compact(match.group(1))
    head = str(pr.get("headRefName") or "").lower()
    head = strip_version_suffix(strip_dependabot_ecosystem(head))
    return head or title.lower()


def is_dependency_pr(pr: dict[str, Any]) -> bool:
    title = str(pr.get("title") or "").lower()
    login = author_login(pr).lower()
    head = str(pr.get("headRefName") or "").lower()
    return "dependabot" in login or "dependabot" in head or "deps" in title or "bump " in title


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="arthexis/arthexis")
    parser.add_argument("--limit", type=int, default=80)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    prs = gh_json(["pr", "list", "--repo", args.repo, "--state", "open", "--limit", str(args.limit), "--json", FIELDS]) or []
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pr in prs:
        if is_dependency_pr(pr):
            groups[package_key(pr)].append(pr)

    duplicates = {
        key: sorted(items, key=lambda item: str(item.get("updatedAt") or ""))
        for key, items in groups.items()
        if len(items) > 1
    }

    if args.json:
        print(json.dumps(duplicates, indent=2))
    else:
        if not duplicates:
            print("No duplicate dependency PR groups found.")
        for key, items in duplicates.items():
            print(f"{key}:")
            for item in items:
                print(f"  #{item.get('number')} {item.get('updatedAt')} {item.get('title')}")
                print(f"    {item.get('url')}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(exc.stderr or str(exc))
        raise SystemExit(exc.returncode) from exc
