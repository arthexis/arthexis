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
            "--bundle",
            choices=kindle_postbox.KINDLE_POSTBOX_BUNDLE_CHOICES,
            default=kindle_postbox.KINDLE_POSTBOX_SUITE_BUNDLE,
            help="Kindle documentation bundle to generate.",
        )
        build_parser.add_argument(
            "--public-library",
            help=(
                "Optional public library directory that should receive a copy "
                "for local postbox daemons to distribute."
            ),
        )
        build_parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report public-library copy status without writing the public copy.",
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
            help=(
                "Directory where generated documentation artifacts should be "
                "written before copying."
            ),
        )
        sync_parser.add_argument(
            "--bundle",
            choices=kindle_postbox.KINDLE_POSTBOX_BUNDLE_CHOICES,
            default=kindle_postbox.KINDLE_POSTBOX_SUITE_BUNDLE,
            help="Kindle documentation bundle to sync.",
        )
        sync_parser.add_argument(
            "--target",
            action="append",
            default=None,
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
            return self._handle_kindle_postbox_build(
                output_dir=output_dir,
                bundle=options["bundle"],
                public_library=(
                    Path(options["public_library"])
                    if options.get("public_library")
                    else None
                ),
                dry_run=options["dry_run"],
                json_output=options["json"],
            )

        if postbox_action == "sync":
            return self._handle_kindle_postbox_sync(
                output_dir=output_dir,
                bundle=options["bundle"],
                json_output=options["json"],
                refresh_usb=options["refresh_usb"],
                dry_run=options["dry_run"],
                targets=options.get("target"),
            )

        raise CommandError(f"Unsupported kindle-postbox action: {postbox_action}")

    def _handle_kindle_postbox_build(
        self,
        *,
        output_dir: Path | None,
        bundle: str,
        public_library: Path | None,
        dry_run: bool,
        json_output: bool,
    ) -> None:
        try:
            built_bundle = kindle_postbox.build_documentation_bundle(
                bundle=bundle,
                output_dir=output_dir,
            )
        except kindle_postbox.DocumentationBundleError as exc:
            raise CommandError(f"Kindle postbox build failed: {exc}") from exc
        publish_result = None
        if public_library is not None:
            publish_result = kindle_postbox.publish_bundle_to_public_library(
                built_bundle,
                public_library,
                dry_run=dry_run,
            )
        if json_output:
            payload = built_bundle.as_dict()
            if publish_result is not None:
                payload["public_library"] = publish_result.as_dict()
            self.stdout.write(json.dumps(payload, sort_keys=True))
            self._raise_failed_public_library_publish(publish_result)
            return
        self.stdout.write(
            f"Kindle postbox {built_bundle.title} built: "
            f"documents={built_bundle.document_count} bytes={built_bundle.byte_count} "
            f"file={built_bundle.output_path}"
        )
        if publish_result is not None:
            self.stdout.write(f"{publish_result.status}: {publish_result.output_path}")
            if publish_result.error:
                self.stderr.write(publish_result.error)
        self._raise_failed_public_library_publish(publish_result)

    def _handle_kindle_postbox_sync(
        self,
        *,
        output_dir: Path | None,
        bundle: str,
        json_output: bool,
        refresh_usb: bool,
        dry_run: bool,
        targets: list[str] | None,
    ) -> None:
        self._local_control_node_or_error()
        if targets is None and not kindle_postbox.usb_inventory.has_usb_inventory_tools():
            raise CommandError(
                "Kindle postbox USB discovery requires lsblk and findmnt on this host; "
                "pass --target to sync an explicit Kindle mount."
            )
        try:
            result = kindle_postbox.sync_to_kindle_postboxes(
                bundle=bundle,
                output_dir=output_dir,
                refresh_usb=refresh_usb,
                dry_run=dry_run,
                targets=targets,
            )
        except kindle_postbox.DocumentationBundleError as exc:
            raise CommandError(f"Kindle postbox build failed: {exc}") from exc
        except kindle_postbox.usb_inventory.UsbInventoryError as exc:
            raise CommandError(f"Kindle postbox USB discovery failed: {exc}") from exc
        failed_targets = self._failed_kindle_targets(result)
        if json_output:
            self.stdout.write(json.dumps(result.as_dict(), sort_keys=True))
            self._raise_failed_kindle_targets(failed_targets)
            return
        self._write_kindle_sync_text(result)
        self._raise_failed_kindle_targets(failed_targets)

    @staticmethod
    def _failed_kindle_targets(
        result: kindle_postbox.KindlePostboxSyncResult,
    ) -> list[kindle_postbox.KindlePostboxTargetResult]:
        return [
            target
            for target in result.targets
            if target.status in {"failed", "missing"}
        ]

    @staticmethod
    def _raise_failed_kindle_targets(
        failed_targets: list[kindle_postbox.KindlePostboxTargetResult],
    ) -> None:
        if failed_targets:
            raise CommandError(
                "Kindle postbox sync failed for "
                f"{len(failed_targets)} target(s)."
            )

    @staticmethod
    def _raise_failed_public_library_publish(
        publish_result: kindle_postbox.KindlePostboxPublishResult | None,
    ) -> None:
        if publish_result is not None and publish_result.status == "failed":
            raise CommandError("Kindle postbox public-library publish failed.")

    def _write_kindle_sync_text(
        self,
        result: kindle_postbox.KindlePostboxSyncResult,
    ) -> None:
        self.stdout.write(
            f"Kindle postbox {result.bundle.title} built: "
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

    def _local_control_node_or_error(self) -> Node:
        node = Node.get_local()
        if node is None:
            raise CommandError("No local node is registered for Kindle postbox sync")
        if not node_is_control(node):
            raise CommandError("Kindle postbox sync is only available on Control nodes")
        return node
