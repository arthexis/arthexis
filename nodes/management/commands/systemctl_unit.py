import subprocess
from typing import Iterable

from django.core.management.base import BaseCommand, CommandError

from nodes.models import SystemdUnit


class Command(BaseCommand):
    """Send systemctl commands to installed systemd unit templates."""

    help = "Run systemctl commands for SystemdUnit templates"

    def add_arguments(self, parser):
        parser.add_argument(
            "action",
            type=str,
            help="systemctl action to execute (e.g. start, stop, restart)",
        )
        parser.add_argument(
            "names",
            nargs="*",
            help="Names of SystemdUnits. If omitted, all installed units are used.",
        )

    def handle(self, *args, **options):
        action: str = options["action"]
        names: Iterable[str] = options["names"]

        if names:
            units = list(SystemdUnit.objects.filter(name__in=names))
            missing = set(names) - {u.name for u in units}
            if missing:
                raise CommandError(f"Unknown SystemdUnit(s): {', '.join(sorted(missing))}")
        else:
            units = list(SystemdUnit.objects.all())

        any_ran = False
        for unit in units:
            if not unit.is_installed():
                self.stderr.write(
                    self.style.WARNING(f"Unit {unit.name} is not installed; skipping")
                )
                continue
            cmd = ["systemctl", action, f"{unit.name}.service"]
            subprocess.run(cmd, check=True)
            self.stdout.write(self.style.SUCCESS(f"Ran: {' '.join(cmd)}"))
            any_ran = True

        if not any_ran:
            raise CommandError("No matching installed SystemdUnit found")
