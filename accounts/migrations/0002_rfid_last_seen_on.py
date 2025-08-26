from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="rfid",
            name="last_seen_on",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
