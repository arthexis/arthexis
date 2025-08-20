from django.core.management.base import BaseCommand, CommandError
from accounts.models import Account, RFID
import csv


class Command(BaseCommand):
    help = "Import RFIDs from CSV"

    def add_arguments(self, parser):
        parser.add_argument("path", help="CSV file to load")
        parser.add_argument(
            "--color",
            choices=["black", "white", "all"],
            default="all",
            help="Import only RFIDs of this color (default: all)",
        )
        parser.add_argument(
            "--released",
            choices=["true", "false", "all"],
            default="all",
            help="Import only RFIDs with this released state (default: all)",
        )

    def handle(self, *args, **options):
        path = options["path"]
        color_filter = options["color"]
        released_filter = options["released"]
        try:
            with open(path, newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                count = 0
                for row in reader:
                    rfid = row.get("rfid", "").strip()
                    accounts = row.get("accounts", "").strip()
                    allowed = row.get("allowed", "True").strip().lower() != "false"
                    color = row.get("color", "black").strip().lower() or "black"
                    released = row.get("released", "False").strip().lower() == "true"
                    if not rfid:
                        continue
                    if color_filter != "all" and color != color_filter:
                        continue
                    if released_filter != "all" and released != (
                        released_filter == "true"
                    ):
                        continue
                    tag, _ = RFID.objects.update_or_create(
                        rfid=rfid.upper(),
                        defaults={
                            "allowed": allowed,
                            "color": color,
                            "released": released,
                        },
                    )
                    if accounts:
                        ids = [int(a) for a in accounts.split(",") if a]
                        tag.accounts.set(Account.objects.filter(id__in=ids))
                    else:
                        tag.accounts.clear()
                    count += 1
        except FileNotFoundError as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(f"Imported {count} tags"))
