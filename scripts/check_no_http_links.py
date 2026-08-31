#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import sys


def _iter_document_paths(repo_root: pathlib.Path) -> list[pathlib.Path]:
    readmes = list(repo_root.glob("README*.md"))
    docs_root = repo_root / "docs"
    docs = []
    if docs_root.exists():
        docs = [
            path
            for path in docs_root.rglob("*")
            if path.is_file() and path.suffix.lower() in {".md", ".rst", ".txt"}
        ]
    return sorted({*readmes, *docs})


def main() -> int:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    offenders = []

    for path in _iter_document_paths(repo_root):
        contents = path.read_text(encoding="utf-8", errors="ignore")
        if "http://" in contents:
            offenders.append(path.relative_to(repo_root))

    if offenders:
        message = ["Found HTTP links in documentation files:"]
        message.extend(f" - {path}" for path in offenders)
        print("\n".join(message))
        return 1

    print("No HTTP links found in README* or docs/ files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
