import os
import subprocess

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from nodes.models import SystemdUnit


class Command(BaseCommand):
    """Install a systemd unit template by name."""

    help = "Install a systemd unit template by writing it to the systemd directory and enabling it"

    def add_arguments(self, parser):
        parser.add_argument("name", type=str, help="Name of the SystemdUnit to install")

    def handle(self, *args, **options):
        name = options["name"]
        try:
            unit = SystemdUnit.objects.get(name=name)
        except SystemdUnit.DoesNotExist as exc:
            raise CommandError(f"SystemdUnit {name!r} does not exist") from exc

        root = getattr(settings, "SYSTEMD_UNIT_ROOT", "/etc/systemd/system")
        os.makedirs(root, exist_ok=True)
        path = os.path.join(root, f"{unit.name}.service")
        with open(path, "w") as fh:
            fh.write(unit.render_unit())

        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "enable", unit.name], check=True)
        subprocess.run(["systemctl", "restart", unit.name], check=True)

        self.stdout.write(self.style.SUCCESS(f"Installed unit {unit.name} to {path}"))
