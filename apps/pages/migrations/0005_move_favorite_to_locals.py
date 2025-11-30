from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("pages", "0004_alter_odoochatbridge_profile"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[migrations.DeleteModel("Favorite")],
            database_operations=[],
        ),
    ]
