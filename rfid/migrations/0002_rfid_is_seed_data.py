from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("rfid", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="rfid",
            name="is_seed_data",
            field=models.BooleanField(default=False),
        ),
    ]
