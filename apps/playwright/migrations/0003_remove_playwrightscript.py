from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("playwright", "0002_migrate_from_selenium"),
    ]

    operations = [
        migrations.DeleteModel(
            name="PlaywrightScript",
        ),
    ]
