from django.core.management.base import BaseCommand

from apps.nodes.models import Node


class Command(BaseCommand):
    help = "Refresh auto-managed features for the local node."

    def handle(self, *args, **options):
        node = Node.get_local()
        if node is None:
            self.stdout.write(
                self.style.WARNING("Local node not found, skipping feature refresh.")
            )
            return

        self.stdout.write(f"Refreshing features for local node {node}...")
        node.refresh_features()
        self.stdout.write(self.style.SUCCESS("Successfully refreshed features."))
