from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("ocpp", "0019_remove_placeholder_chargers"),
    ]

    operations = [
        migrations.CreateModel(
            name="DataTransferMessage",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False, auto_created=True, verbose_name="ID")),
                ("connector_id", models.PositiveIntegerField(null=True, blank=True)),
                (
                    "direction",
                    models.CharField(
                        max_length=16,
                        choices=[
                            ("cp_to_csms", "Charge Point → CSMS"),
                            ("csms_to_cp", "CSMS → Charge Point"),
                        ],
                    ),
                ),
                ("ocpp_message_id", models.CharField(max_length=64)),
                ("vendor_id", models.CharField(max_length=255, blank=True)),
                ("message_id", models.CharField(max_length=255, blank=True)),
                ("payload", models.JSONField(default=dict, blank=True)),
                ("status", models.CharField(max_length=64, blank=True)),
                ("response_data", models.JSONField(null=True, blank=True)),
                ("error_code", models.CharField(max_length=64, blank=True)),
                ("error_description", models.TextField(blank=True)),
                ("error_details", models.JSONField(null=True, blank=True)),
                ("responded_at", models.DateTimeField(null=True, blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "charger",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="data_transfer_messages",
                        to="ocpp.charger",
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="datatransfermessage",
            index=models.Index(
                fields=["ocpp_message_id"],
                name="ocpp_datatr_ocpp_me_70d17f_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="datatransfermessage",
            index=models.Index(
                fields=["vendor_id"], name="ocpp_datatr_vendor__59e1c7_idx"
            ),
        ),
    ]
