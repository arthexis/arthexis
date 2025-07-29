from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from accounts.models import RFID
import csv

User = get_user_model()

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
                    user = row.get('user', '').strip()
                    allowed = row.get('allowed', 'True').strip().lower() != 'false'
                    if not rfid:
                        continue
                    user_obj = None
                    if user:
                        try:
                            user_obj = User.objects.get(username=user)
                        except User.DoesNotExist:
                            raise CommandError(f'Unknown user {user}')
                    RFID.objects.update_or_create(
                        rfid=rfid.upper(),
                        defaults={'user': user_obj, 'allowed': allowed},
                    )
                    count += 1
        except FileNotFoundError as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(f'Imported {count} tags'))
