from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder


class Command(BaseCommand):
    help = "Clear recorded OCPP migrations and apply them again."

    def handle(self, *args, **options):
        recorder = MigrationRecorder(connection)
        if recorder.has_table():
            recorder.migration_qs.filter(app="ocpp").delete()

        with connection.cursor() as cursor:
            tables = [
                t for t in connection.introspection.table_names() if t.startswith("ocpp_")
            ]
            for table in tables:
                cursor.execute(f"DROP TABLE {connection.ops.quote_name(table)}")

        call_command("migrate", "ocpp", fake_initial=True)

