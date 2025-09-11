#!/usr/bin/env python3
"""Verify migration state: ensure no new migrations required and no merge migrations present."""
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    # Detect merge migrations
    known_merges = {REPO_ROOT / "core" / "migrations" / "0009_merge_20250901_2230.py"}
    for path in REPO_ROOT.rglob("migrations/*merge*.py"):
        if path not in known_merges:
            print(f"Merge migrations detected: {path}", file=sys.stderr)
            return 1

    # Ensure no new migrations are needed
    apps = ["nodes", "core", "ocpp", "awg", "pages", "news", "app", "man"]
    try:
        subprocess.run(
            ["python", "manage.py", "makemigrations", "--check", "--dry-run", *apps],
            cwd=REPO_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except subprocess.CalledProcessError:
        print(
            "Uncommitted model changes detected. Please rewrite the latest migration.",
            file=sys.stderr,
        )
        return 1

    print("Migrations check passed.")
    return 0


if __name__ == "__main__":  # pragma: no cover - script entry
    raise SystemExit(main())
