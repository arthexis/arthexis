# Generated by Django 5.2.4 on 2025-07-29 00:16

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ocpp", "0002_add_charger_model"),
    ]

    operations = [
        migrations.AddField(
            model_name="charger",
            name="last_heartbeat",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="charger",
            name="last_meter_values",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
