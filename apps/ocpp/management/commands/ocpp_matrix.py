"""Report retained OCPP action-matrix implementation status."""

from django.core.management.base import BaseCommand, CommandError

from apps.ocpp.domain.matrix import support_matrix, unimplemented_actions


class Command(BaseCommand):
    help = "Report implementation status for every retained OCPP action contract."

    def handle(self, *args, **options) -> None:
        entries = support_matrix()
        missing = unimplemented_actions()
        for entry in entries:
            contract = entry.contract
            state = "implemented" if entry.implemented else "unimplemented"
            self.stdout.write(
                f"{contract.version.value} {contract.direction.value} "
                f"{contract.action}: {state}"
            )
        self.stdout.write(f"{len(entries) - len(missing)} / {len(entries)} implemented")
        if missing:
            raise CommandError("The retained OCPP action matrix is incomplete.")
