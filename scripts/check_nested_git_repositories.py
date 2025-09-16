#!/usr/bin/env python3
"""Fail when nested Git repositories are present in the tree."""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(BASE_DIR))

from utils.git_checks import find_nested_git_repositories


def main() -> int:
    nested = find_nested_git_repositories(BASE_DIR)
    if nested:
        for path in nested:
            print(f"Nested git repository detected at {path}")
        return 1
    print("No nested git repositories detected.")
    return 0


if __name__ == "__main__":  # pragma: no cover - script entry
    raise SystemExit(main())
