from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0010_databaseconfig"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="databaseconfig",
            name="password",
        ),
    ]
