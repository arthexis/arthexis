#!/usr/bin/env python
"""Compute a hash for the current Django migrations tree."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
APPS_DIR = BASE_DIR / "apps"


def iter_migration_files():
    for migrations_dir in sorted(APPS_DIR.glob("*/migrations")):
        if not migrations_dir.is_dir():
            continue
        for file_path in sorted(migrations_dir.rglob("*")):
            if file_path.is_dir():
                continue
            if "__pycache__" in file_path.parts:
                continue
            if file_path.suffix in {".pyc", ".pyo"}:
                continue
            yield file_path


def compute_migrations_hash() -> str:
    digest = hashlib.md5()
    for file_path in iter_migration_files():
        digest.update(str(file_path.relative_to(BASE_DIR)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def main() -> int:
    try:
        print(compute_migrations_hash())
    except Exception as exc:  # pragma: no cover - defensive catch
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
