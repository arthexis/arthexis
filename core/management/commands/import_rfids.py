from django.core.management.base import BaseCommand, CommandError
from core.models import EnergyAccount, RFID
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

    def handle(self, *args, **options):
        path = options["path"]
        color_filter = options["color"].upper()
        released_filter = options["released"]
        try:
            with open(path, newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                count = 0
                for row in reader:
                    rfid = row.get("rfid", "").strip()
                    energy_accounts = row.get("energy_accounts", "").strip()
                    allowed = row.get("allowed", "True").strip().lower() != "false"
                    color = (
                        row.get("color", RFID.BLACK).strip().upper() or RFID.BLACK
                    )
                    released = row.get("released", "False").strip().lower() == "true"
                    if not rfid:
                        continue
                    if color_filter != "ALL" and color != color_filter:
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
                    if energy_accounts:
                        ids = [int(a) for a in energy_accounts.split(",") if a]
                        tag.energy_accounts.set(EnergyAccount.objects.filter(id__in=ids))
                    else:
                        tag.energy_accounts.clear()
                    count += 1
        except FileNotFoundError as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(f"Imported {count} tags"))
