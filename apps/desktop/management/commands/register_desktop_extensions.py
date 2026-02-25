"""Register desktop extensions in the host operating system."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.desktop.models import RegisteredExtension
from apps.desktop.services import register_extension_with_os


class Command(BaseCommand):
    """Register enabled `RegisteredExtension` records."""

    help = "Register enabled desktop extensions with the operating system."

    def handle(self, *args, **options):
        """Run registration for all enabled extension mappings."""

        for extension in RegisteredExtension.objects.filter(is_enabled=True):
            result = register_extension_with_os(extension)
            writer = self.stdout.write if result.success else self.stderr.write
            writer(f"{extension.extension}: {result.message}")
