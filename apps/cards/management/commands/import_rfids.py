from django.core.management import call_command
from django.core.management.base import BaseCommand

from apps.cards.models import RFID


class Command(BaseCommand):
    """Deprecated wrapper for `rfid import`."""

    help = "[DEPRECATED] Use `manage.py rfid import` instead."

    def add_arguments(self, parser):
        parser.add_argument("path", help="CSV file to import")
        parser.add_argument("--color", choices=[c[0] for c in RFID.COLOR_CHOICES] + ["ALL"], default="ALL", help="Import only RFIDs of this color code (default: all)")
        parser.add_argument("--released", choices=["true", "false", "all"], default="all", help="Import only RFIDs with this released state (default: all)")
        parser.add_argument(
            "--account-field",
            choices=["id", "name"],
            default="id",
            help=(
                "Read customer accounts from the specified field (default: id). "
                "Use 'name' to link accounts by their names, creating missing ones."
            ),
        )

    def handle(self, *args, **options):
        self.stderr.write(self.style.WARNING("import_rfids is deprecated; use `manage.py rfid import` instead."))
        call_command(
            "rfid",
            "import",
            options["path"],
            color=options["color"],
            released=options["released"],
            account_field=options["account_field"],
            stdout=self.stdout,
            stderr=self.stderr,
        )
