from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("energy", "0002_initial"),
        ("maps", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[migrations.DeleteModel(name="Location")],
            database_operations=[],
        ),
        migrations.CreateModel(
            name="Location",
            fields=[],
            options={"abstract": False, "proxy": True, "indexes": [], "constraints": []},
            bases=("maps.location",),
        ),
    ]
