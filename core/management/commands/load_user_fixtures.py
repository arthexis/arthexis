from django.core.management.base import BaseCommand
from core.user_data import load_user_fixtures


class Command(BaseCommand):
    help = "Load personal user data fixtures"

    def handle(self, *args, **options):
        load_user_fixtures()
