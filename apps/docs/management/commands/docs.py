from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.docs import kindle_postbox
from apps.nodes.models import Node
from apps.nodes.roles import node_is_control


class Command(BaseCommand):
    """Build and distribute suite documentation artifacts."""

    help = "Documentation operations, including Kindle postbox export and sync."

    def add_arguments(self, parser) -> None:
        subparsers = parser.add_subparsers(dest="action")
        subparsers.required = True

        postbox_parser = subparsers.add_parser(
            "kindle-postbox",
            help="Build or sync the Kindle postbox suite documentation bundle.",
        )
        postbox_subparsers = postbox_parser.add_subparsers(dest="postbox_action")
        postbox_subparsers.required = True

        build_parser = postbox_subparsers.add_parser(
            "build",
            help="Generate the suite documentation bundle in the local work directory.",
        )
        build_parser.add_argument(
            "--output-dir",
            help="Directory where generated documentation artifacts should be written.",
        )
        build_parser.add_argument(
            "--json",
            action="store_true",
            help="Emit machine-readable JSON output.",
        )

        sync_parser = postbox_subparsers.add_parser(
            "sync",
            help="Copy the suite documentation bundle to Kindle postbox targets.",
        )
        sync_parser.add_argument(
            "--output-dir",
            help="Directory where generated documentation artifacts should be written before copying.",
        )
        sync_parser.add_argument(
            "--target",
            action="append",
            default=[],
            help="Explicit mounted Kindle root to receive the bundle. Repeat for multiple targets.",
        )
        sync_parser.add_argument(
            "--refresh-usb",
            action="store_true",
            help="Refresh USB inventory before resolving kindle-postbox targets.",
        )
        sync_parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Generate the bundle and report copy destinations without writing to targets.",
        )
        sync_parser.add_argument(
            "--json",
            action="store_true",
            help="Emit machine-readable JSON output.",
        )

    def handle(self, *args, **options) -> None:
        if options["action"] == "kindle-postbox":
            return self._handle_kindle_postbox(**options)
        raise CommandError(f"Unsupported docs action: {options['action']}")

    def _handle_kindle_postbox(self, **options) -> None:
        output_dir = (
            Path(options["output_dir"]) if options.get("output_dir") else None
        )
        postbox_action = options["postbox_action"]
        if postbox_action == "build":
            bundle = kindle_postbox.build_suite_documentation_bundle(
                output_dir=output_dir,
            )
            if options["json"]:
                self.stdout.write(json.dumps(bundle.as_dict(), sort_keys=True))
                return
            self.stdout.write(
                "Kindle postbox documentation built: "
                f"documents={bundle.document_count} bytes={bundle.byte_count} "
                f"file={bundle.output_path}"
            )
            return

        if postbox_action == "sync":
            self._local_control_node_or_error()
            result = kindle_postbox.sync_to_kindle_postboxes(
                output_dir=output_dir,
                refresh_usb=options["refresh_usb"],
                dry_run=options["dry_run"],
                targets=options.get("target") or None,
            )
            if options["json"]:
                self.stdout.write(json.dumps(result.as_dict(), sort_keys=True))
                return
            self.stdout.write(
                "Kindle postbox documentation built: "
                f"documents={result.bundle.document_count} bytes={result.bundle.byte_count} "
                f"file={result.bundle.output_path}"
            )
            if not result.targets:
                self.stdout.write("No Kindle postbox targets found.")
                return
            for target in result.targets:
                destination = target.output_path or target.root_path
                self.stdout.write(f"{target.status}: {destination}")
                if target.error:
                    self.stderr.write(target.error)
            return

        raise CommandError(f"Unsupported kindle-postbox action: {postbox_action}")

    def _local_control_node_or_error(self) -> Node:
        node = Node.get_local()
        if node is None:
            raise CommandError("No local node is registered for Kindle postbox sync")
        if not node_is_control(node):
            raise CommandError("Kindle postbox sync is only available on Control nodes")
        return node
