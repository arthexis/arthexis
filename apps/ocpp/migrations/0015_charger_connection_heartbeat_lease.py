from datetime import timedelta

from django.db import migrations, models
from django.utils import timezone


def backfill_lease_expiry(apps, schema_editor):
    ChargerConnection = apps.get_model("ocpp", "ChargerConnection")
    for connection in ChargerConnection.objects.all().iterator():
        connection.lease_expires_at = connection.last_seen_at + timedelta(seconds=900)
        connection.save(update_fields=("lease_expires_at",))


class Migration(migrations.Migration):
    dependencies = [
        ("ocpp", "0014_monitoring_reported_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="chargerconnection",
            name="heartbeat_interval_seconds",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="chargerconnection",
            name="lease_expires_at",
            field=models.DateTimeField(default=timezone.now, db_index=True),
        ),
        migrations.RunPython(backfill_lease_expiry, migrations.RunPython.noop),
    ]
