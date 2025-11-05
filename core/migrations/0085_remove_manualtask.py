from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0084_manualtask"),
        ("teams", "0012_manualtask"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[migrations.DeleteModel("ManualTask")],
            database_operations=[],
        )
    ]
