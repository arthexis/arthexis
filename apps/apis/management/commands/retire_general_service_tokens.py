"""Retire expired general service JWT tokens."""

from django.core.management.base import BaseCommand

from apps.apis.models import GeneralServiceToken


class Command(BaseCommand):
    """Retire expired general service tokens and print the number updated."""

    help = "Retire expired general service tokens."

    def handle(self, *args, **options):
        retired = GeneralServiceToken.retire_expired_tokens()
        self.stdout.write(str(retired))
