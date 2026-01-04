from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0011_netmessage_expires_at"),
        ("services", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[migrations.DeleteModel(name="NodeService")],
            database_operations=[],
        )
    ]
