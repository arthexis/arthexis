from django.core.management.base import BaseCommand

from apps.widgets.services import sync_registered_widgets


class Command(BaseCommand):
    help = "Ensure database rows exist for registered widgets and zones."

    def handle(self, *args, **options):
        sync_registered_widgets()
