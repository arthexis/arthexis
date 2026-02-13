"""Manage manually-pinned local IP addresses in ``.locks/local_ips.lck``."""

from __future__ import annotations

import ipaddress
import json
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from config.settings_helpers import load_local_ip_lock


class Command(BaseCommand):
    """Add or remove IP addresses from the local IP lock file."""

    help = (
        "Manage .locks/local_ips.lck entries manually. "
        "Use --add and/or --remove for one or more addresses."
    )

    def add_arguments(self, parser) -> None:
        """Register command-line arguments."""

        parser.add_argument(
            "--add",
            dest="add",
            action="append",
            default=[],
            help="IP address to add (can be provided multiple times).",
        )
        parser.add_argument(
            "--remove",
            dest="remove",
            action="append",
            default=[],
            help="IP address to remove (can be provided multiple times).",
        )

    def handle(self, *args, **options) -> None:
        """Apply requested additions/removals and persist the lock file."""

        add_values = options.get("add") or []
        remove_values = options.get("remove") or []

        if not add_values and not remove_values:
            raise CommandError("Provide at least one --add or --remove value.")

        base_dir = Path(getattr(settings, "BASE_DIR", Path.cwd()))
        existing = load_local_ip_lock(base_dir)

        adds = {self._normalize_address(raw) for raw in add_values}
        removes = {self._normalize_address(raw) for raw in remove_values}

        updated = existing.union(adds)
        updated.difference_update(removes)

        payload = {
            "addresses": sorted(updated),
            "updated_at": datetime.now(tz=timezone.utc).isoformat(),
        }

        lock_path = base_dir / ".locks" / "local_ips.lck"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

        self.stdout.write(
            self.style.SUCCESS(
                "Updated local IP lock: "
                f"{len(adds)} added, {len(removes)} removed, {len(updated)} total."
            )
        )

    def _normalize_address(self, value: str) -> str:
        """Normalize and validate an IP address value from the CLI."""

        candidate = (value or "").strip().strip("[]")
        if not candidate:
            raise CommandError("IP address values cannot be empty.")
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError as exc:
            raise CommandError(f"Invalid IP address: {value}") from exc
