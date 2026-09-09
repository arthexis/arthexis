from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.nodes.models import Node


class Command(BaseCommand):
    """Ensure the current host has a resolvable local node registration."""

    help = "Create or refresh the local SELF node registration."

    def handle(self, *args, **options):
        del args, options
        node, created = Node.register_current(notify_peers=False)

        # A previous Node.get_local() miss may have been cached in this process.
        # Clear it before checking the lifecycle invariant we just repaired.
        Node._local_cache.clear()
        local_node = Node.get_local()
        if local_node is None or local_node.pk != node.pk:
            raise CommandError(
                "Local node registration could not be resolved after registration."
            )

        action = "created" if created else "refreshed"
        self.stdout.write(
            self.style.SUCCESS(
                f"Local node registration {action}: {local_node.public_endpoint or local_node.hostname}"
            )
        )
