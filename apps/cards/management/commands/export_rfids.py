from django.core.management import call_command
from django.core.management.base import BaseCommand

from apps.cards.models import RFID


class Command(BaseCommand):
    """Deprecated wrapper for `rfid export`."""

    help = "[DEPRECATED] Use `manage.py rfid export` instead."

    def add_arguments(self, parser):
        parser.add_argument("path", nargs="?", help="CSV file to write (stdout when omitted)")
        parser.add_argument("--color", choices=[c[0] for c in RFID.COLOR_CHOICES] + ["ALL"], default=RFID.BLACK, help="Filter RFIDs by color code (default: {})".format(RFID.BLACK))
        parser.add_argument("--released", choices=["true", "false", "all"], default="all", help="Filter RFIDs by released state (default: all)")
        parser.add_argument(
            "--account-field",
            choices=["id", "name"],
            default="id",
            help=(
                "Include customer accounts using the selected field (default: id). "
                "Use 'name' to export the related account names."
            ),
        )

    def handle(self, *args, **options):
        self.stderr.write(self.style.WARNING("export_rfids is deprecated; use `manage.py rfid export` instead."))
        call_args = ["rfid", "export"]
        if options.get("path") is not None:
            call_args.append(options["path"])
        call_command(
            *call_args,
            color=options["color"],
            released=options["released"],
            account_field=options["account_field"],
            stdout=self.stdout,
            stderr=self.stderr,
        )
