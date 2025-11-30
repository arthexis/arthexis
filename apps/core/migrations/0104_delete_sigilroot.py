from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0103_delete_todo"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[migrations.DeleteModel(name="SigilRoot")],
            database_operations=[],
        )
    ]
