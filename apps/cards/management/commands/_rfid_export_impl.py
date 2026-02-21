from django.core.management.base import BaseCommand
from apps.cards.models import RFID
from apps.cards.rfid_import_export import account_column_for_field, serialize_accounts
import csv


class Command(BaseCommand):
    help = "Export RFIDs to CSV"

    def add_arguments(self, parser):
        parser.add_argument(
            "path", nargs="?", help="File to write CSV to; stdout if omitted"
        )
        parser.add_argument(
            "--color",
            choices=[c[0] for c in RFID.COLOR_CHOICES] + ["ALL"],
            default=RFID.BLACK,
            help="Filter RFIDs by color code (default: {})".format(RFID.BLACK),
        )
        parser.add_argument(
            "--released",
            choices=["true", "false", "all"],
            default="all",
            help="Filter RFIDs by released state (default: all)",
        )
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
        path = options["path"]
        qs = RFID.objects.all()
        color = options["color"].upper()
        released = options["released"]
        account_field = options["account_field"]
        if color != "ALL":
            qs = qs.filter(color=color)
        if released != "all":
            qs = qs.filter(released=(released == "true"))
        qs = qs.order_by("rfid")
        accounts_column = account_column_for_field(account_field)

        def format_accounts(tag):
            return serialize_accounts(tag, account_field)

        rows = (
            (
                t.rfid,
                t.custom_label,
                format_accounts(t),
                str(t.allowed),
                t.color,
                str(t.released),
            )
            for t in qs
        )
        if path:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(
                    [
                        "rfid",
                        "custom_label",
                        accounts_column,
                        "allowed",
                        "color",
                        "released",
                    ]
                )
                writer.writerows(rows)
        else:
            writer = csv.writer(self.stdout)
            writer.writerow(
                [
                    "rfid",
                    "custom_label",
                    accounts_column,
                    "allowed",
                    "color",
                    "released",
                ]
            )
            writer.writerows(rows)
        self.stdout.write(self.style.SUCCESS("Exported {} tags".format(qs.count())))
