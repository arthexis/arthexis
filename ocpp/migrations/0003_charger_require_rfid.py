# Generated by Django 5.2.4 on 2025-07-29 00:18

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ocpp", "0002_add_charger_model"),
    ]

    operations = [
        migrations.AddField(
            model_name="charger",
            name="require_rfid",
            field=models.BooleanField(default=False),
        ),
    ]
