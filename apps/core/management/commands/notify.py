from django.core.management.base import BaseCommand
from apps.core.notifications import notify


class Command(BaseCommand):
    """Send a message to the LCD or GUI notification fallback."""

    help = "Send a message to the LCD or GUI notification fallback"

    def add_arguments(self, parser):
        parser.add_argument("subject", help="First line of the message")
        parser.add_argument(
            "body",
            nargs="?",
            default="",
            help="Optional second line of the message",
        )

    def handle(self, *args, **options):
        subject = options["subject"]
        body = options["body"]
        notify(subject=subject, body=body)
        self.stdout.write(self.style.SUCCESS("Notification sent"))
