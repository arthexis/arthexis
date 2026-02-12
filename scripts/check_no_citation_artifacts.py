#!/usr/bin/env python3
"""Fail when repository documentation contains inline citation artifacts."""

from __future__ import annotations

import pathlib
import sys

CITATION_TOKEN = "【F:"


def _iter_document_paths(repo_root: pathlib.Path) -> list[pathlib.Path]:
    """Return Markdown/ReST/text docs that should be free of citation artifacts."""
    readmes = list(repo_root.glob("README*.md"))
    docs_root = repo_root / "docs"
    docs: list[pathlib.Path] = []
    if docs_root.exists():
        docs = [
            path
            for path in docs_root.rglob("*")
            if path.is_file() and path.suffix.lower() in {".md", ".rst", ".txt"}
        ]
    return sorted({*readmes, *docs})


def main() -> int:
    """Run the citation-artifact check and return a shell exit code."""
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    offenders: list[pathlib.Path] = []

    for path in _iter_document_paths(repo_root):
        contents = path.read_text(encoding="utf-8", errors="ignore")
        if CITATION_TOKEN in contents:
            offenders.append(path.relative_to(repo_root))

    if offenders:
        message = [f"Found disallowed citation token {CITATION_TOKEN!r} in documentation files:"]
        message.extend(f" - {path}" for path in offenders)
        print("\n".join(message))
        return 1

    print("No citation artifacts found in README* or docs/ files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
