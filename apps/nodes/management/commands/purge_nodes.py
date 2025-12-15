"""Management command to purge soft-deleted and duplicate nodes.

The optional ``--force`` flag is reserved for superusers and permanently
removes seed nodes instead of marking them as soft-deleted. Use with care.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from apps.base.models import Entity
from apps.nodes.models import Node


class Command(BaseCommand):
    """Remove soft-deleted nodes and deduplicate remaining entries."""

    help = (
        "Delete nodes flagged as soft-deleted and remove duplicate nodes, "
        "keeping the most recent duplicate entry. Use --force (superusers only) "
        "to permanently delete seed nodes instead of soft-deleting them."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "Permanently delete seed nodes (superusers only); without this "
                "flag, seed nodes are only soft-deleted."
            ),
        )
        parser.add_argument(
            "--superuser",
            help=(
                "Username of the superuser authorizing --force deletions. "
                "Required when using --force."
            ),
        )

    def handle(self, *args, **options):
        force = bool(options.get("force"))
        if options.get("superuser") and not force:
            self.stdout.write(
                self.style.WARNING("--superuser was provided but --force was not; ignoring."),
            )
        if force:
            if not options.get("superuser"):
                raise CommandError("--force requires --superuser to be provided.")
            superuser = self._require_superuser(options.get("superuser"))
            self.stdout.write(
                self.style.WARNING(
                    "Force purge requested by superuser %s. Seed nodes will be permanently removed."
                    % superuser.username
                )
            )

        soft_deleted = Node.all_objects.filter(is_deleted=True)
        _, soft_delete_counts = self._delete_nodes(soft_deleted, force=force)
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

        _, duplicate_delete_counts = self._delete_nodes(
            Node.objects.filter(pk__in=duplicate_ids), force=force
        )
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

    def _delete_nodes(self, queryset, *, force: bool):
        if not force:
            return queryset.delete()

        delete_count = 0
        for node in queryset:
            self._force_delete_node(node)
            delete_count += 1

        return delete_count, {Node._meta.label: delete_count}

    def _force_delete_node(self, node: Node) -> None:
        if node.is_seed_data:
            super(Entity, node).delete()
        else:
            node.delete()

    def _require_superuser(self, username: str | None):
        User = get_user_model()
        manager = getattr(User, "all_objects", User.objects)
        candidates = manager.filter(is_superuser=True)
        if username:
            candidates = candidates.filter(username=username)
        superuser = candidates.first()
        if not superuser:
            raise CommandError(
                "--force requires a valid superuser (provide with --superuser)."
            )
        return superuser
