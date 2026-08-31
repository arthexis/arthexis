from django.core.management.base import BaseCommand

from apps.ocpp.maintenance import reset_cached_statuses


class Command(BaseCommand):
    """Reset persisted cached charger statuses."""

    help = "Reset persisted cached charger statuses when the OCPP schema is available."

    def handle(self, *args, **options):
        cleared = reset_cached_statuses()
        self.stdout.write(f"Cleared cached charger statuses for {cleared} charge points.")
