from django.core.management.base import BaseCommand
from accounts.models import RFID
import csv


class Command(BaseCommand):
    help = "Export RFIDs to CSV"

    def add_arguments(self, parser):
        parser.add_argument(
            "path", nargs="?", help="File to write CSV to; stdout if omitted"
        )
        parser.add_argument(
            "--color",
            choices=["black", "white", "all"],
            default="black",
            help="Filter RFIDs by color (default: black)",
        )
        parser.add_argument(
            "--released",
            choices=["true", "false", "all"],
            default="all",
            help="Filter RFIDs by released state (default: all)",
        )

    def handle(self, *args, **options):
        path = options["path"]
        qs = RFID.objects.all()
        color = options["color"]
        released = options["released"]
        if color != "all":
            qs = qs.filter(color=color)
        if released != "all":
            qs = qs.filter(released=(released == "true"))
        qs = qs.order_by("rfid")
        rows = (
            (
                t.rfid,
                ",".join(str(a.id) for a in t.accounts.all()),
                str(t.allowed),
                t.color,
                str(t.released),
            )
            for t in qs
        )
        if path:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(["rfid", "accounts", "allowed", "color", "released"])
                writer.writerows(rows)
        else:
            writer = csv.writer(self.stdout)
            writer.writerow(["rfid", "accounts", "allowed", "color", "released"])
            writer.writerows(rows)
        self.stdout.write(self.style.SUCCESS("Exported {} tags".format(qs.count())))
