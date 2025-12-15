"""Management command to purge soft-deleted and duplicate nodes."""
from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.nodes.models import Node


class Command(BaseCommand):
    """Remove soft-deleted nodes and deduplicate remaining entries."""

    help = (
        "Delete nodes flagged as soft-deleted and remove duplicate nodes, "
        "keeping the most recent duplicate entry."
    )

    def handle(self, *args, **options):
        soft_deleted = Node.all_objects.filter(is_deleted=True)
        _, soft_delete_counts = soft_deleted.delete()
        soft_deleted_count = soft_delete_counts.get(Node._meta.label, 0)

        kept_keys: set[str] = set()
        duplicate_ids: list[int] = []

        for node in Node.objects.order_by("-id"):
            dedup_key = self._deduplication_key(node)
            if not dedup_key:
                continue
            if dedup_key in kept_keys:
                duplicate_ids.append(node.pk)
            else:
                kept_keys.add(dedup_key)

        _, duplicate_delete_counts = Node.objects.filter(pk__in=duplicate_ids).delete()
        duplicate_count = duplicate_delete_counts.get(Node._meta.label, 0)

        messages: list[str] = []
        if soft_deleted_count:
            suffix = "" if soft_deleted_count == 1 else "s"
            messages.append(f"Removed {soft_deleted_count} soft-deleted node{suffix}")
        if duplicate_count:
            suffix = "" if duplicate_count == 1 else "s"
            messages.append(f"Deleted {duplicate_count} duplicate node{suffix}")

        if messages:
            self.stdout.write(self.style.SUCCESS("; ".join(messages)))
        else:
            self.stdout.write("No nodes purged.")

    def _deduplication_key(self, node: Node) -> str:
        mac = (node.mac_address or "").strip()
        if mac:
            return mac.lower()
        hostname = (node.hostname or "").strip()
        if hostname:
            return hostname.lower()
        return ""
