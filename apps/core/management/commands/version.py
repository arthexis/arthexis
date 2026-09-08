from django.core.management.base import BaseCommand

from apps.core.system.identity import version


class Command(BaseCommand):
    help = "Show the running Arthexis version"

    def handle(self, *args, **options):
        self.stdout.write(version())
