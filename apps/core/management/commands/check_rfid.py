"""Deprecated wrapper for ``rfid check`` with positional UID support."""

from django.core.management.base import BaseCommand

from apps.cards.management.commands._rfid_check_impl import (
    add_check_arguments,
    run_check_command,
)


class Command(BaseCommand):
    help = "Deprecated: use `python manage.py rfid check --uid <value>` instead."

    def add_arguments(self, parser):
        add_check_arguments(parser, include_positional_value=True)

    def handle(self, *args, **options):
        self.stderr.write(
            self.style.WARNING(
                "`check_rfid` is deprecated. Use `python manage.py rfid check --uid <value>`."
            )
        )
        run_check_command(self, options, positional_value=options.get("value"))
