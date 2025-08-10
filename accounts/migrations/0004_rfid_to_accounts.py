from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_brand_is_seed_data_evmodel_alter_vehicle_model"),
    ]

    operations = [
        migrations.CreateModel(
            name="RFID",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "rfid",
                    models.CharField(
                        max_length=8,
                        unique=True,
                        verbose_name="RFID",
                        validators=[
                            django.core.validators.RegexValidator(
                                "^[0-9A-Fa-f]{8}$",
                                message="RFID must be 8 hexadecimal digits",
                            )
                        ],
                    ),
                ),
                ("allowed", models.BooleanField(default=True)),
                ("added_on", models.DateTimeField(auto_now_add=True)),
                ("is_seed_data", models.BooleanField(default=False)),
            ],
            options={
                "verbose_name": "RFID",
                "verbose_name_plural": "RFIDs",
                "db_table": "accounts_rfid",
            },
        ),
        migrations.AddField(
            model_name="account",
            name="rfids",
            field=models.ManyToManyField(blank=True, related_name="accounts", to="accounts.rfid"),
        ),
    ]
