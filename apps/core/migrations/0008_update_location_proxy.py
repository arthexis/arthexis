from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0007_admincommandresult_is_deleted_and_more"),
        ("energy", "0003_move_location_to_maps"),
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
