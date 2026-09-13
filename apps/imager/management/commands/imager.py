"""Retired compatibility command for the removed Raspberry Pi imager."""

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    """Refuse retired image-management operations during the migration bridge."""

    # Retained for one bridge release because a central role-profile smoke test
    # still discovers the command. No image-management operation is available.
    help = "Build and safely write Raspberry Pi 4B image artifacts."

    def handle(self, *args, **options):
        raise CommandError(
            "Arthexis Raspberry Pi image generation and burning have been retired; "
            "use official Raspberry Pi provisioning tools instead."
        )
