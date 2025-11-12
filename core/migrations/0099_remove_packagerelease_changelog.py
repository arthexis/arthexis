from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0098_location_model"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="packagerelease",
            name="changelog",
        ),
    ]
