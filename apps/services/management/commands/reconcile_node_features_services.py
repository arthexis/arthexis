from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.services.lifecycle import reconcile_node_features_and_services


class Command(BaseCommand):
    """Refresh node auto-detected features and reconcile lifecycle service state."""

    help = (
        "Refresh local node auto features, then reconcile lifecycle lock files and "
        "systemd unit lock records."
    )

    def handle(self, *args, **options) -> None:
        """Execute node/service reconciliation using the lifecycle service layer."""

        config = reconcile_node_features_and_services()
        self.stdout.write(
            self.style.SUCCESS(
                "Reconciled lifecycle services for "
                f"{len(config.systemd_units)} configured unit(s)."
            )
        )
