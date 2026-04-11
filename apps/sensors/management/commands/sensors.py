"""Manual sensor operations for operators and administrators."""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from apps.sensors.tasks import scan_usb_trackers


class Command(BaseCommand):
    """Provide CLI entrypoints for sensor workflows."""

    help = "Sensor operations: run passive USB tracker scans on demand."

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest="action")
        subparsers.required = True

        scan_parser = subparsers.add_parser(
            "scan-usb-trackers",
            help="Run a one-time passive USB tracker scan.",
        )
        scan_parser.add_argument(
            "--json",
            action="store_true",
            help="Emit machine-readable JSON output.",
        )

    def handle(self, *args, **options):
        action = options["action"]
        if action == "scan-usb-trackers":
            return self._handle_scan_usb_trackers(**options)
        raise CommandError(f"Unsupported action: {action}")

    def _handle_scan_usb_trackers(self, **options):
        result = scan_usb_trackers()
        if options["json"]:
            self.stdout.write(json.dumps(result, sort_keys=True))
            return

        self.stdout.write(
            "USB tracker scan complete: "
            f"scanned={result['scanned']} matched={result['matched']} failed={result['failed']}"
        )
