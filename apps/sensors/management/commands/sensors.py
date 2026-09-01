"""Manual sensor operations for operators and administrators."""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from apps.nodes.models import Node
from apps.nodes.roles import node_is_control
from apps.sensors import usb_inventory
from apps.sensors.tasks import scan_usb_trackers


class Command(BaseCommand):
    """Provide CLI entrypoints for sensor workflows."""

    help = "Sensor operations: run USB tracker scans and inspect USB inventory."

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
        inventory_parser = subparsers.add_parser(
            "usb-inventory",
            help="Refresh and query local USB block-device inventory.",
        )
        inventory_subparsers = inventory_parser.add_subparsers(dest="usb_action")
        inventory_subparsers.required = True

        inventory_refresh = inventory_subparsers.add_parser(
            "refresh",
            help="Refresh the local USB inventory state file.",
        )
        inventory_refresh.add_argument(
            "--json",
            action="store_true",
            help="Emit machine-readable JSON output.",
        )
        inventory_list = inventory_subparsers.add_parser(
            "list",
            help="List USB inventory state, refreshing if no state file exists.",
        )
        inventory_list.add_argument(
            "--refresh",
            action="store_true",
            help="Refresh before listing devices.",
        )
        inventory_list.add_argument(
            "--json",
            action="store_true",
            help="Emit machine-readable JSON output.",
        )
        claimed_path = inventory_subparsers.add_parser(
            "claimed-path",
            help="Print mount or device paths claimed for a local USB role.",
        )
        claimed_path.add_argument("--role", required=True)
        claimed_path.add_argument(
            "--refresh",
            action="store_true",
            help="Refresh before resolving claims.",
        )
        claimed_path.add_argument(
            "--json",
            action="store_true",
            help="Emit machine-readable JSON output.",
        )
        path_claims = inventory_subparsers.add_parser(
            "path-claims",
            help="Print USB roles claimed by the supplied path.",
        )
        path_claims.add_argument("path")
        path_claims.add_argument(
            "--refresh",
            action="store_true",
            help="Refresh before resolving claims.",
        )
        path_claims.add_argument(
            "--json",
            action="store_true",
            help="Emit machine-readable JSON output.",
        )

    def handle(self, *args, **options):
        action = options["action"]
        if action == "scan-usb-trackers":
            return self._handle_scan_usb_trackers(**options)
        if action == "usb-inventory":
            return self._handle_usb_inventory(**options)
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


    def _handle_usb_inventory(self, **options):
        self._local_control_node_or_error()
        if not usb_inventory.has_usb_inventory_tools():
            raise CommandError("USB inventory requires lsblk and findmnt on this host")

        usb_action = options["usb_action"]
        if usb_action == "refresh":
            payload = usb_inventory.refresh_inventory()
            if options["json"]:
                self.stdout.write(json.dumps(payload, sort_keys=True))
                return
            self.stdout.write(
                "USB inventory refreshed: "
                f"devices={len(payload.get('devices', []))} state={usb_inventory.state_path()}"
            )
            return
        if usb_action == "list":
            payload = usb_inventory.state_or_refresh(refresh=options["refresh"])
            if options["json"]:
                self.stdout.write(json.dumps(payload, sort_keys=True))
                return
            devices = payload.get("devices", [])
            self.stdout.write(f"USB inventory devices: {len(devices)}")
            for device in devices:
                if not isinstance(device, dict):
                    self.stderr.write("Skipping malformed USB inventory entry.")
                    continue
                claims = (
                    ",".join(
                        self._safe_terminal_text(claim)
                        for claim in (device.get("claims") or [])
                    )
                    or "-"
                )
                path = self._safe_terminal_text(
                    device.get("mountpoint") or device.get("path") or "-"
                )
                label = (
                    device.get("label")
                    or device.get("model")
                    or device.get("name")
                    or "-"
                )
                label = self._safe_terminal_text(label)
                self.stdout.write(f"{label} {path} claims={claims}")
            return
        if usb_action == "claimed-path":
            paths = usb_inventory.claimed_paths(
                options["role"],
                refresh=options["refresh"],
            )
            if options["json"]:
                self.stdout.write(
                    json.dumps(
                        {"role": options["role"], "paths": paths}, sort_keys=True
                    )
                )
                return
            for path in paths:
                self.stdout.write(self._safe_terminal_text(path))
            return
        if usb_action == "path-claims":
            claims = usb_inventory.path_claims(
                options["path"],
                refresh=options["refresh"],
            )
            if options["json"]:
                self.stdout.write(
                    json.dumps(
                        {"path": options["path"], "claims": claims}, sort_keys=True
                    )
                )
                return
            for claim in claims:
                self.stdout.write(self._safe_terminal_text(claim))
            return
        raise CommandError(f"Unsupported usb-inventory action: {usb_action}")

    @staticmethod
    def _safe_terminal_text(value):
        """Return terminal-safe text for console output by JSON-escaping control characters.

        Converts ``value`` to ``str`` and applies ``json.dumps(..., ensure_ascii=False)[1:-1]``
        so control characters, quotes, and backslashes are escaped while Unicode remains
        readable. DEL and C1 controls are escaped after JSON rendering because
        ``ensure_ascii=False`` leaves them raw. Example: ``"line\n\x1b[31mred"``
        becomes ``"line\\n\\u001b[31mred"``.

        This is for terminal/console rendering only and is not HTML, SQL, or shell escaping.
        """
        rendered = json.dumps(str(value), ensure_ascii=False)[1:-1]
        return "".join(
            f"\\u{ord(character):04x}"
            if character == "\x7f" or "\x80" <= character <= "\x9f"
            else character
            for character in rendered
        )

    def _local_control_node_or_error(self):
        node = Node.get_local()
        if node is None:
            raise CommandError("No local node is registered for USB inventory")
        if not node_is_control(node):
            raise CommandError("USB inventory is only available on Control nodes")
        return node
