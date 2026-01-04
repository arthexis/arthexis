from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("nodes", "0011_netmessage_expires_at"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[migrations.DeleteModel(name="SSHAccount")],
        )
    ]
