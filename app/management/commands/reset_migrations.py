from pathlib import Path
import shutil

from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder


class Command(BaseCommand):
    help = "Remove all migration files for local apps and create fresh initial migrations."

    def handle(self, *args, **options):
        base_dir = Path(settings.BASE_DIR)
        recorder = MigrationRecorder(connection)

        for app_config in apps.get_app_configs():
            app_path = Path(app_config.path)
            try:
                app_path.relative_to(base_dir)
            except ValueError:
                continue

            migrations_path = app_path / "migrations"
            if not migrations_path.exists():
                continue

            for item in migrations_path.iterdir():
                if item.name == "__init__.py":
                    continue
                if item.is_file():
                    item.unlink()
                else:
                    shutil.rmtree(item)

            if recorder.has_table():
                recorder.migration_qs.filter(app=app_config.label).delete()

            self.stdout.write(self.style.WARNING(f"Cleared migrations for {app_config.label}"))

        call_command("makemigrations", interactive=False)
        self.stdout.write(self.style.SUCCESS("Created new initial migrations."))
