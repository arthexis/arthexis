# Generated by Django 5.2.4 on 2025-07-29 01:17

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0006_rename_rfid_field"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="rfid",
            options={"verbose_name": "RFID", "verbose_name_plural": "RFIDs"},
        ),
        migrations.AlterField(
            model_name="rfid",
            name="rfid",
            field=models.CharField(
                max_length=8,
                unique=True,
                validators=[
                    django.core.validators.RegexValidator(
                        "^[0-9A-Fa-f]{8}$", message="RFID must be 8 hexadecimal digits"
                    )
                ],
                verbose_name="RFID",
            ),
        ),
    ]
