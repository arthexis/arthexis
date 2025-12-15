"""Management command to purge soft-deleted and duplicate nodes."""
from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.nodes.models import Node


class Command(BaseCommand):
    """Remove soft-deleted nodes and deduplicate remaining entries."""

    help = (
        "Delete nodes flagged as soft-deleted and remove duplicate nodes, keeping the "
        "most recent duplicate entry. Nodes lacking both MAC address and hostname are "
        "reported and left untouched unless the --remove-anonymous flag is provided."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--remove-anonymous",
            action="store_true",
            dest="remove_anonymous",
            help="Also delete nodes missing both MAC address and hostname.",
        )

    def handle(self, *args, **options):
        remove_anonymous: bool = options.get("remove_anonymous", False)

        soft_deleted = Node.all_objects.filter(is_deleted=True)
        _, soft_delete_counts = soft_deleted.delete()
        soft_deleted_count = soft_delete_counts.get(Node._meta.label, 0)

        kept_keys: set[str] = set()
        duplicate_ids: list[int] = []
        nodes_missing_keys: list[Node] = []

        for node in Node.objects.order_by("-id"):
            dedup_key = self._deduplication_key(node)
            if not dedup_key:
                nodes_missing_keys.append(node)
                continue
            if dedup_key in kept_keys:
                duplicate_ids.append(node.pk)
            else:
                kept_keys.add(dedup_key)

        _, duplicate_delete_counts = Node.objects.filter(pk__in=duplicate_ids).delete()
        duplicate_count = duplicate_delete_counts.get(Node._meta.label, 0)

        anonymous_delete_counts: dict[str, int] | None = None
        if remove_anonymous and nodes_missing_keys:
            _, anonymous_delete_counts = Node.objects.filter(
                pk__in=[node.pk for node in nodes_missing_keys]
            ).delete()

        messages: list[str] = []
        if soft_deleted_count:
            suffix = "" if soft_deleted_count == 1 else "s"
            messages.append(f"Removed {soft_deleted_count} soft-deleted node{suffix}")
        if duplicate_count:
            suffix = "" if duplicate_count == 1 else "s"
            messages.append(f"Deleted {duplicate_count} duplicate node{suffix}")
        if anonymous_delete_counts:
            anonymous_count = anonymous_delete_counts.get(Node._meta.label, 0)
            if anonymous_count:
                suffix = "" if anonymous_count == 1 else "s"
                messages.append(
                    f"Deleted {anonymous_count} anonymous node{suffix} missing deduplication keys"
                )

        if messages:
            self.stdout.write(self.style.SUCCESS("; ".join(messages)))
        else:
            self.stdout.write("No nodes purged.")

        if nodes_missing_keys and not remove_anonymous:
            skipped_descriptions = "; ".join(
                self._format_anonymous_node(node) for node in nodes_missing_keys
            )
            self.stdout.write(
                self.style.WARNING(
                    "Skipped nodes missing deduplication keys: " f"{skipped_descriptions}"
                )
            )

    def _deduplication_key(self, node: Node) -> str:
        mac = (node.mac_address or "").strip()
        if mac:
            return mac.lower()
        hostname = (node.hostname or "").strip()
        if hostname:
            return hostname.lower()
        return ""

    def _format_anonymous_node(self, node: Node) -> str:
        mac = (node.mac_address or "").strip() or "<missing>"
        hostname = (node.hostname or "").strip() or "<missing>"
        return f"id={node.pk}, mac={mac}, hostname={hostname}"
