from django.core.management.base import BaseCommand, CommandError
from apps.cards.models import RFID
from apps.cards.rfid_import_export import account_column_for_field, parse_accounts
import csv


class Command(BaseCommand):
    help = "Import RFIDs from CSV"

    def add_arguments(self, parser):
        parser.add_argument("path", help="CSV file to load")
        parser.add_argument(
            "--color",
            choices=[c[0] for c in RFID.COLOR_CHOICES] + ["ALL"],
            default="ALL",
            help="Import only RFIDs of this color code (default: all)",
        )
        parser.add_argument(
            "--released",
            choices=["true", "false", "all"],
            default="all",
            help="Import only RFIDs with this released state (default: all)",
        )
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
        path = options["path"]
        color_filter = options["color"].upper()
        released_filter = options["released"]
        account_field = options["account_field"]
        try:
            with open(path, newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                count = 0
                for row in reader:
                    rfid = row.get("rfid", "").strip()
                    accounts_column = account_column_for_field(account_field)
                    energy_accounts = row.get(accounts_column, "")
                    custom_label = row.get("custom_label", "").strip()
                    allowed = row.get("allowed", "True").strip().lower() != "false"
                    color = row.get("color", RFID.BLACK).strip().upper() or RFID.BLACK
                    released = row.get("released", "False").strip().lower() == "true"
                    if not rfid:
                        continue
                    if color_filter != "ALL" and color != color_filter:
                        continue
                    if released_filter != "all" and released != (
                        released_filter == "true"
                    ):
                        continue
                    tag, _ = RFID.update_or_create_from_code(
                        rfid,
                        {
                            "custom_label": custom_label,
                            "allowed": allowed,
                            "color": color,
                            "released": released,
                        },
                    )
                    row_context = {
                        accounts_column: energy_accounts,
                        "customer_accounts": row.get("customer_accounts", ""),
                        "customer_account_names": row.get("customer_account_names", ""),
                        "energy_accounts": row.get("energy_accounts", ""),
                        "energy_account_names": row.get("energy_account_names", ""),
                    }
                    accounts = parse_accounts(row_context, account_field)
                    if accounts:
                        tag.energy_accounts.set(accounts)
                    else:
                        tag.energy_accounts.clear()
                    count += 1
        except FileNotFoundError as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(f"Imported {count} tags"))
