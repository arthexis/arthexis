from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0112_remove_constellation_udp_periodic_task"),
    ]

    operations = [
        migrations.AddField(
            model_name="rfid",
            name="expiry_date",
            field=models.DateField(
                blank=True,
                help_text="Optional expiration date for this RFID card.",
                null=True,
            ),
        ),
    ]

