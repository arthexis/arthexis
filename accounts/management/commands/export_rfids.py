from django.core.management.base import BaseCommand
from rfid.models import RFID
import csv

class Command(BaseCommand):
    help = "Export RFIDs to CSV"

    def add_arguments(self, parser):
        parser.add_argument('path', nargs='?', help='File to write CSV to; stdout if omitted')

    def handle(self, *args, **options):
        path = options['path']
        qs = RFID.objects.all().order_by('rfid')
        rows = (
            (
                t.rfid,
                ",".join(str(a.id) for a in t.accounts.all()),
                str(t.allowed),
            )
            for t in qs
        )
        if path:
            with open(path, 'w', newline='', encoding='utf-8') as fh:
                writer = csv.writer(fh)
                writer.writerow(['rfid', 'accounts', 'allowed'])
                writer.writerows(rows)
        else:
            writer = csv.writer(self.stdout)
            writer.writerow(['rfid', 'accounts', 'allowed'])
            writer.writerows(rows)
        self.stdout.write(self.style.SUCCESS('Exported {} tags'.format(qs.count())))
