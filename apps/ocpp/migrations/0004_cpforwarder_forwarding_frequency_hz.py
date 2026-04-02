from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ocpp", "0003_controloperationevent"),
    ]

    operations = [
        migrations.AddField(
            model_name="cpforwarder",
            name="forwarding_frequency_hz",
            field=models.FloatField(
                default=0.0,
                help_text=(
                    "Forward message batches at this frequency. Set to 0 for immediate "
                    "real-time forwarding."
                ),
            ),
        ),
    ]
