import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.core.system.identity import node_role


class Command(BaseCommand):
    help = "Show the canonical Arthexis node role"

    def add_arguments(self, parser):
        parser.add_argument(
            "--debug",
            action="store_true",
            help="Show the inputs used to resolve the current role",
        )

    def handle(self, *args, **options):
        role = node_role()
        self.stdout.write(role)
        if not options["debug"]:
            return

        base_dir = Path(settings.BASE_DIR)
        role_lock = base_dir / ".locks" / "role.lck"
        try:
            lock_value = role_lock.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            lock_value = "<unavailable>" if role_lock.exists() else "<missing>"

        self.stdout.write("Debug:")
        self.stdout.write(f"  resolved role: {role}")
        self.stdout.write(
            f"  settings.NODE_ROLE: {getattr(settings, 'NODE_ROLE', '<unset>')}"
        )
        self.stdout.write(f"  NODE_ROLE: {os.environ.get('NODE_ROLE', '<unset>')}")
        self.stdout.write(
            f"  GWAY_SERVICE_PROFILE: {os.environ.get('GWAY_SERVICE_PROFILE', '<unset>')}"
        )
        self.stdout.write(f"  role lock: {role_lock}")
        self.stdout.write(f"  role lock exists: {role_lock.exists()}")
        self.stdout.write(f"  role lock value: {lock_value}")
