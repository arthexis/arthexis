import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cards", "0002_initial"),
        ("ocpp", "0005_move_rfid_session_attempt"),
        ("energy", "0004_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="RFIDSessionAttempt",
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
                        ("is_seed_data", models.BooleanField(default=False, editable=False)),
                        ("is_user_data", models.BooleanField(default=False, editable=False)),
                        ("is_deleted", models.BooleanField(default=False, editable=False)),
                        (
                            "rfid",
                            models.CharField(blank=True, max_length=255, verbose_name="RFID"),
                        ),
                        (
                            "status",
                            models.CharField(
                                choices=[("accepted", "Accepted"), ("rejected", "Rejected")],
                                max_length=16,
                            ),
                        ),
                        ("attempted_at", models.DateTimeField(auto_now_add=True)),
                        (
                            "account",
                            models.ForeignKey(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.SET_NULL,
                                related_name="rfid_attempts",
                                to="energy.customeraccount",
                            ),
                        ),
                        (
                            "charger",
                            models.ForeignKey(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="rfid_attempts",
                                to="ocpp.charger",
                            ),
                        ),
                        (
                            "transaction",
                            models.ForeignKey(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.SET_NULL,
                                related_name="rfid_attempts",
                                to="ocpp.transaction",
                            ),
                        ),
                    ],
                    options={
                        "ordering": ["-attempted_at"],
                        "verbose_name": "RFID Session Attempt",
                        "verbose_name_plural": "RFID Session Attempts",
                        "db_table": "ocpp_rfidsessionattempt",
                    },
                )
            ],
            database_operations=[],
        )
    ]
