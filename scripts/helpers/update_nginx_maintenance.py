#!/usr/bin/env python3
"""Ensure nginx configs serve the maintenance fallback page."""
from __future__ import annotations

import sys
from pathlib import Path

from apps.nginx.maintenance import update_config


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: update_nginx_maintenance.py /path/to/conf", file=sys.stderr)
        return 1

    path = Path(sys.argv[1])
    try:
        return update_config(path)
    except PermissionError:
        print(
            f"Permission denied updating {path}. Run this script with sudo or "
            "ensure the file is writable by the current user.",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:  # pragma: no cover - defensive guard
        print(f"Failed to update {path}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
