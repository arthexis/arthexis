from django.core.management.base import BaseCommand

from apps.core.system.identity import node_role


class Command(BaseCommand):
    help = "Show the canonical Arthexis node role"

    def handle(self, *args, **options):
        self.stdout.write(node_role())
