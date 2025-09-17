from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0024_aplead"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="user",
            name="has_charger",
        ),
    ]
