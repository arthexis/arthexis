from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0034_purge_net_messages"),
        ("teams", "0016_move_email_profiles"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[migrations.DeleteModel(name="EmailOutbox")],
        )
    ]
