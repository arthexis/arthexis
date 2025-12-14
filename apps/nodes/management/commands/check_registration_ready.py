from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Deprecated. Use 'registration_ready' instead."

    def handle(self, *args, **options):
        self.stderr.write(
            self.style.WARNING(
                "'check_registration_ready' is deprecated; use 'registration_ready' instead."
            )
        )
        call_command("registration_ready")
