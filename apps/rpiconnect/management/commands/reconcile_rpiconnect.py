"""Reconcile rpiconnect deployment state with remotely reported status."""

from django.core.management.base import BaseCommand

from apps.rpiconnect.services.ingestion_service import (
    IngestionService,
    default_reconciliation_status_fetcher,
)


class Command(BaseCommand):
    help = "Reconcile rpiconnect deployment statuses using remote status hooks."

    def handle(self, *args, **options):
        service = IngestionService()
        result = service.reconcile_deployments(status_fetcher=default_reconciliation_status_fetcher)
        self.stdout.write(
            self.style.SUCCESS(
                f"Reconciliation complete: checked={result.checked}, repaired={result.repaired}"
            )
        )
