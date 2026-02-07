#!/usr/bin/env python3
"""Persist discovered local IP addresses to the ArtHExis lock directory."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def _ensure_repo_on_path(base_dir: Path) -> None:
    """Ensure the repo root is on sys.path for module imports."""
    base_dir_str = str(base_dir)
    if base_dir_str not in sys.path:
        sys.path.insert(0, base_dir_str)


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: local_ip_lock.py <base-dir>", file=sys.stderr)
        return 1

    base_dir = Path(sys.argv[1]).resolve()
    _ensure_repo_on_path(base_dir)

    from config.settings_helpers import discover_local_ip_addresses, load_local_ip_lock
    lock_dir = base_dir / ".locks"
    lock_path = lock_dir / "local_ips.lck"

    existing = load_local_ip_lock(base_dir)
    discovered = discover_local_ip_addresses()
    addresses = sorted(existing.union(discovered))

    payload = {
        "addresses": addresses,
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
    }

    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
