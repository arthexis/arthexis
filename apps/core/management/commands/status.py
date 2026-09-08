from django.core.management.base import BaseCommand

from apps.core.system.identity import status


class Command(BaseCommand):
    help = "Show compact Arthexis application health"

    def handle(self, *args, **options):
        result = status()
        self.stdout.write(result)
        if result != "GOOD":
            raise SystemExit(1)
