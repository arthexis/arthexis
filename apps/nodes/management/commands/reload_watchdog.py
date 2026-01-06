from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from scripts import ap_watchdog


class Command(BaseCommand):
    help = "Regenerate the AP watchdog nmcli template from the current system state."

    def handle(self, *args, **options):
        base_dir = Path(settings.BASE_DIR)
        connections = ap_watchdog.snapshot_nmcli_template(base_dir)
        self.stdout.write(
            self.style.SUCCESS(
                f"Refreshed watchdog template with {len(connections)} connection(s)."
            )
        )
