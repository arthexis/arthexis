from django.core.management.base import BaseCommand

from apps.skills.services import sync_db_to_filesystem, sync_filesystem_to_db


class Command(BaseCommand):
    help = "Sync agent skill SKILL.md records between filesystem and database."

    def add_arguments(self, parser):
        parser.add_argument("--direction", choices=["to-db", "to-files"], required=True)

    def handle(self, *args, **options):
        direction = options["direction"]
        if direction == "to-db":
            count = sync_filesystem_to_db()
        else:
            count = sync_db_to_filesystem()
        self.stdout.write(self.style.SUCCESS(f"Synced {count} agent skill records ({direction})."))
