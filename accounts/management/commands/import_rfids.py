from django.core.management.base import BaseCommand, CommandError
from rfid.models import RFID
from accounts.models import Account
import csv

class Command(BaseCommand):
    help = "Import RFIDs from CSV"

    def add_arguments(self, parser):
        parser.add_argument('path', help='CSV file to load')

    def handle(self, *args, **options):
        path = options['path']
        try:
            with open(path, newline='', encoding='utf-8') as fh:
                reader = csv.DictReader(fh)
                count = 0
                for row in reader:
                    rfid = row.get('rfid', '').strip()
                    accounts = row.get('accounts', '').strip()
                    allowed = row.get('allowed', 'True').strip().lower() != 'false'
                    if not rfid:
                        continue
                    tag, _ = RFID.objects.update_or_create(
                        rfid=rfid.upper(), defaults={"allowed": allowed}
                    )
                    if accounts:
                        ids = [int(a) for a in accounts.split(",") if a]
                        tag.accounts.set(Account.objects.filter(id__in=ids))
                    else:
                        tag.accounts.clear()
                    count += 1
        except FileNotFoundError as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(f'Imported {count} tags'))
