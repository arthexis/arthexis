from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Deprecated wrapper for `rfid watch`."""

    help = "[DEPRECATED] Use `manage.py rfid watch` (or `manage.py rfid watch --stop`)."

    def add_arguments(self, parser):
        parser.add_argument("--stop", action="store_true", help="Stop the always-on watcher instead of starting it")

    def handle(self, *args, **options):
        self.stderr.write(self.style.WARNING("watch_rfid is deprecated; use `manage.py rfid watch` instead."))
        call_command("rfid", "watch", stop=options["stop"], stdout=self.stdout, stderr=self.stderr)
