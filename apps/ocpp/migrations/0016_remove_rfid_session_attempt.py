from __future__ import annotations

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("ocpp", "0015_stationmodel_documents_bucket_and_more"),
        ("cards", "0006_rfid_attempt"),
    ]

    operations = [
        migrations.DeleteModel(name="RFIDSessionAttempt"),
    ]
