from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Backward-compatible alias for the ``password`` management command."""

    help = "Deprecated alias for `password --temporary`."

    def add_arguments(self, parser):
        parser.add_argument("identifier")
        parser.add_argument("--expires-in", type=int)
        parser.add_argument("--allow-change", action="store_true")
        parser.add_argument("--create", action="store_true")
        parser.add_argument("--update", action="store_true")
        parser.add_argument("--staff", action="store_true")
        parser.add_argument("--superuser", action="store_true")

    def handle(self, *args, **options):
        """Delegate to the ``password`` command with temporary mode enabled."""

        call_command(
            "password",
            options["identifier"],
            temporary=True,
            **({"expires_in": options["expires_in"]} if options["expires_in"] is not None else {}),
            allow_change=options["allow_change"],
            create=options["create"],
            update=options["update"],
            staff=options["staff"],
            superuser=options["superuser"],
            stdout=self.stdout,
            stderr=self.stderr,
        )
