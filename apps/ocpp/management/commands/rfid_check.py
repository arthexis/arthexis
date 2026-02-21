"""Deprecated wrapper for ``rfid check``."""

from django.core.management.base import BaseCommand

from apps.cards.management.commands._rfid_check_impl import (
    add_check_arguments,
    run_check_command,
)


class Command(BaseCommand):
    help = "Deprecated: use `python manage.py rfid check` instead."

    def add_arguments(self, parser):
        add_check_arguments(parser)

    def handle(self, *args, **options):
        self.stderr.write(
            self.style.WARNING("`rfid_check` is deprecated. Use `python manage.py rfid check`.")
        )
        run_check_command(self, options)
