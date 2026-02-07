#!/usr/bin/env python3
"""Persist discovered local IP addresses to the ArtHExis lock directory."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from config.settings_helpers import discover_local_ip_addresses


def _load_existing_addresses(lock_path: Path) -> set[str]:
    if not lock_path.exists():
        return set()

    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()

    if isinstance(payload, dict):
        addresses = payload.get("addresses", [])
    else:
        addresses = payload

    if isinstance(addresses, str):
        addresses = addresses.splitlines()

    if not isinstance(addresses, list):
        return set()

    return {str(entry).strip() for entry in addresses if str(entry).strip()}


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: local_ip_lock.py <base-dir>", file=sys.stderr)
        return 1

    base_dir = Path(sys.argv[1]).resolve()
    lock_dir = base_dir / ".locks"
    lock_path = lock_dir / "local_ips.lck"

    existing = _load_existing_addresses(lock_path)
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
