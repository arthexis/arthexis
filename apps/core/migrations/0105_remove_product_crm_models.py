from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0104_delete_sigilroot"),
        ("crms", "0001_initial"),
        ("teams", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name="OdooProfile"),
                migrations.DeleteModel(name="Product"),
            ],
            database_operations=[],
        )
    ]
