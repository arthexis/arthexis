from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ocpp", "0023_protocol_operation_reconciliation"),
    ]

    operations = [
        migrations.CreateModel(
            name="OcppPolicy",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "charger_admission_mode",
                    models.CharField(
                        choices=[("open", "Open"), ("restricted", "Restricted")],
                        default="open",
                        max_length=16,
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
    ]
