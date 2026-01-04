#!/usr/bin/env python3
"""Detect invisible characters in repository files.

This script is intended as a lightweight static check to prevent invisible
Unicode code points from slipping into the codebase. Invisible characters are
tricky to spot during review and can be abused for obfuscation. The checker
walks text files under the repository root (excluding common binary and
generated directories) and reports any occurrences with line and column
numbers.
"""
from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass
from typing import Iterator

# Repository root path
REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

# Directories that should not be scanned.
IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "env",
    "venv",
    "node_modules",
    "media",
    "static",
    "__pycache__",
}

# Binary-like suffixes to skip when scanning for invisible characters.
BINARY_SUFFIXES = {
    ".7z",
    ".bin",
    ".bmp",
    ".db",
    ".dll",
    ".dylib",
    ".eot",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".jpeg",
    ".jpg",
    ".mo",
    ".mp3",
    ".mp4",
    ".otf",
    ".pdf",
    ".png",
    ".so",
    ".sqlite",
    ".svg",
    ".tar",
    ".tiff",
    ".ttf",
    ".webm",
    ".webp",
    ".woff",
    ".woff2",
    ".xz",
    ".zip",
}

# Invisible characters worth flagging for review.
INVISIBLE_CHARACTERS = {
    "\u200b": "ZERO WIDTH SPACE",
    "\u200c": "ZERO WIDTH NON-JOINER",
    "\u200d": "ZERO WIDTH JOINER",
    "\u2060": "WORD JOINER",
    "\ufeff": "ZERO WIDTH NO-BREAK SPACE",
    "\u180e": "MONGOLIAN VOWEL SEPARATOR",
}


@dataclass(frozen=True)
class InvisibleCharacterFinding:
    path: pathlib.Path
    line: int
    column: int
    character: str
    description: str

    def render(self, root: pathlib.Path) -> str:
        relative = self.path.relative_to(root)
        codepoint = f"U+{ord(self.character):04X}"
        return f"{relative}:{self.line}:{self.column} -> {self.description} ({codepoint})"


def _iter_candidate_files(root: pathlib.Path) -> Iterator[pathlib.Path]:
    for current_dir, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in IGNORED_DIRS]
        current_path = pathlib.Path(current_dir)
        for file_name in files:
            path = current_path / file_name
            if path.suffix.lower() in BINARY_SUFFIXES:
                continue
            yield path


def _scan_text(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")


def scan_file_for_invisible_characters(path: pathlib.Path) -> list[InvisibleCharacterFinding]:
    contents = _scan_text(path)
    findings: list[InvisibleCharacterFinding] = []
    for line_number, line in enumerate(contents.splitlines(), start=1):
        for column_number, char in enumerate(line, start=1):
            if char in INVISIBLE_CHARACTERS:
                findings.append(
                    InvisibleCharacterFinding(
                        path=path,
                        line=line_number,
                        column=column_number,
                        character=char,
                        description=INVISIBLE_CHARACTERS[char],
                    )
                )
    return findings


def find_invisible_characters(root: pathlib.Path) -> list[InvisibleCharacterFinding]:
    results: list[InvisibleCharacterFinding] = []
    for path in _iter_candidate_files(root):
        results.extend(scan_file_for_invisible_characters(path))
    return results


def main() -> int:
    findings = find_invisible_characters(REPO_ROOT)
    if findings:
        print("Invisible characters detected:")
        for finding in findings:
            print(f" - {finding.render(REPO_ROOT)}")
        return 1

    print("No invisible characters detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
