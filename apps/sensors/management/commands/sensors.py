"""Manual sensor operations for operators and administrators."""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from apps.nodes.models import Node
from apps.sensors.models import UsbPortMapping
from apps.sensors.tasks import scan_usb_trackers
from apps.sensors.usb_lcd import normalize_usb_lcd_label, write_usb_lcd_status


class Command(BaseCommand):
    """Provide CLI entrypoints for sensor workflows."""

    help = "Sensor operations: run USB tracker scans and manage USB LCD status."

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
        set_parser = subparsers.add_parser(
            "set-usb-lcd-port",
            help="Configure the LCD label and local inventory source for a USB port.",
        )
        set_parser.add_argument("--port", type=int, required=True, choices=range(1, 5))
        set_parser.add_argument(
            "--source-type",
            required=True,
            choices=[choice for choice, _label in UsbPortMapping.SourceType.choices],
            help="Local inventory source used to detect connection state.",
        )
        set_parser.add_argument(
            "--source-id",
            required=True,
            help="USB tracker slug, recording-device identifier, or video-device identifier.",
        )
        set_parser.add_argument(
            "--label",
            default="",
            help="Optional LCD label; values are uppercased and truncated to 7 chars.",
        )
        set_parser.add_argument("--description", default="")
        set_parser.add_argument(
            "--inactive",
            action="store_true",
            help="Store the mapping but hide it from the LCD screen.",
        )
        set_parser.add_argument(
            "--json",
            action="store_true",
            help="Emit machine-readable JSON output.",
        )
        clear_parser = subparsers.add_parser(
            "clear-usb-lcd-port",
            help="Remove the LCD mapping for a USB port.",
        )
        clear_parser.add_argument(
            "--port", type=int, required=True, choices=range(1, 5)
        )
        clear_parser.add_argument(
            "--json",
            action="store_true",
            help="Emit machine-readable JSON output.",
        )
        write_parser = subparsers.add_parser(
            "write-usb-lcd-status",
            help="Write the current USB LCD lock file.",
        )
        write_parser.add_argument(
            "--scan-trackers",
            action="store_true",
            help="Run passive USB tracker scanning before writing the LCD status.",
        )
        write_parser.add_argument(
            "--json",
            action="store_true",
            help="Emit machine-readable JSON output.",
        )

    def handle(self, *args, **options):
        action = options["action"]
        if action == "scan-usb-trackers":
            return self._handle_scan_usb_trackers(**options)
        if action == "set-usb-lcd-port":
            return self._handle_set_usb_lcd_port(**options)
        if action == "clear-usb-lcd-port":
            return self._handle_clear_usb_lcd_port(**options)
        if action == "write-usb-lcd-status":
            return self._handle_write_usb_lcd_status(**options)
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

    def _handle_set_usb_lcd_port(self, **options):
        source_id = str(options["source_id"]).strip()
        if not source_id:
            raise CommandError("--source-id cannot be blank")
        node = self._local_node_or_error()

        label = normalize_usb_lcd_label(options.get("label"), fallback="")
        mapping, created = UsbPortMapping.objects.update_or_create(
            node=node,
            port_number=options["port"],
            defaults={
                "source_type": options["source_type"],
                "source_identifier": source_id,
                "label": label,
                "description": options.get("description") or "",
                "is_active": not options.get("inactive", False),
            },
        )
        lcd_result = write_usb_lcd_status(node=node)
        result = {
            "created": created,
            "node": node.pk,
            "port": mapping.port_number,
            "source_type": mapping.source_type,
            "source_identifier": mapping.source_identifier,
            "label": mapping.label,
            "is_active": mapping.is_active,
            "lcd": lcd_result,
        }
        if options["json"]:
            self.stdout.write(json.dumps(result, sort_keys=True))
            return
        state = "created" if created else "updated"
        self.stdout.write(
            f"USB LCD port {mapping.port_number} {state}: "
            f"{mapping.source_type}:{mapping.source_identifier} label={mapping.label or '<auto>'}"
        )

    def _handle_clear_usb_lcd_port(self, **options):
        node = self._local_node_or_error()
        deleted, _ = UsbPortMapping.objects.filter(
            node=node, port_number=options["port"]
        ).delete()
        lcd_result = write_usb_lcd_status(node=node)
        result = {
            "deleted": bool(deleted),
            "node": node.pk,
            "port": options["port"],
            "lcd": lcd_result,
        }
        if options["json"]:
            self.stdout.write(json.dumps(result, sort_keys=True))
            return
        self.stdout.write(
            f"USB LCD port {options['port']} cleared"
            if deleted
            else f"USB LCD port {options['port']} had no mapping"
        )

    def _handle_write_usb_lcd_status(self, **options):
        tracker_result = scan_usb_trackers() if options.get("scan_trackers") else None
        result = write_usb_lcd_status()
        if tracker_result is not None:
            result["trackers"] = tracker_result
        if options["json"]:
            self.stdout.write(json.dumps(result, sort_keys=True))
            return
        if result["written"]:
            self.stdout.write(
                "USB LCD status written: "
                f"configured={result['configured']} connected={result['connected']} "
                f"lock={result['lock_file']}"
            )
            return
        self.stdout.write("USB LCD status cleared: no active mappings configured")

    def _local_node_or_error(self):
        node = Node.get_local()
        if node is None:
            raise CommandError("No local node is registered for USB LCD mappings")
        return node
