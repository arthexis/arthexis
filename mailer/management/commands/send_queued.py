from django.core.management.base import BaseCommand

from mailer.views import send_queued


class Command(BaseCommand):
    help = "Send all queued emails"

    def handle(self, *args, **options):
        send_queued()
        self.stdout.write(self.style.SUCCESS("Sent queued emails"))
