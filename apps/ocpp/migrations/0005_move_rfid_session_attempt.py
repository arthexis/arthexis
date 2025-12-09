from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("ocpp", "0004_remove_rfid_proxy"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[migrations.DeleteModel(name="RFIDSessionAttempt")],
            database_operations=[],
        ),
    ]
