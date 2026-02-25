"""Open a file through a registered desktop extension mapping."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.desktop.models import RegisteredExtension
from apps.desktop.services import run_registered_extension


class Command(BaseCommand):
    """Dispatch an opened filename to a configured Django command."""

    help = "Execute a registered extension command for an opened file."

    def add_arguments(self, parser):
        """Define command line arguments."""

        parser.add_argument("--extension-id", required=True, type=int)
        parser.add_argument("--filename", default=None)

    def handle(self, *args, **options):
        """Execute the mapping and surface command output and failures."""

        extension_id = options["extension_id"]
        filename = options["filename"]

        try:
            extension = RegisteredExtension.objects.get(pk=extension_id, is_enabled=True)
        except RegisteredExtension.DoesNotExist as exc:
            raise CommandError(f"Registered extension {extension_id} is not enabled.") from exc

        result = run_registered_extension(extension, filename)
        if result.stdout:
            self.stdout.write(result.stdout.rstrip())
        if result.stderr:
            self.stderr.write(result.stderr.rstrip())
        if result.returncode != 0:
            raise CommandError(
                f"Registered extension command failed with exit code {result.returncode}."
            )
