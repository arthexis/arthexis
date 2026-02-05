from __future__ import annotations

from django.db import migrations, models


def copy_ocpp_attempts(apps, schema_editor):
    RFIDAttempt = apps.get_model("cards", "RFIDAttempt")
    try:
        LegacyAttempt = apps.get_model("ocpp", "RFIDSessionAttempt")
    except LookupError:
        return
    batch = []
    for attempt in LegacyAttempt.objects.all().iterator():
        status = attempt.status
        authenticated = None
        if status == "accepted":
            authenticated = True
        elif status == "rejected":
            authenticated = False
        batch.append(
            RFIDAttempt(
                rfid=attempt.rfid,
                status=status,
                authenticated=authenticated,
                source="ocpp",
                attempted_at=attempt.attempted_at,
                charger_id=attempt.charger_id,
                account_id=attempt.account_id,
                transaction_id=attempt.transaction_id,
            )
        )
    if batch:
        RFIDAttempt.objects.bulk_create(batch, batch_size=500)


def noop(apps, schema_editor):
    return


class Migration(migrations.Migration):
    dependencies = [
        ("cards", "0005_carddesign_cardset_rfid_card_designs_and_more"),
        ("ocpp", "0015_stationmodel_documents_bucket_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="RFIDAttempt",
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
                ("rfid", models.CharField(blank=True, max_length=255, verbose_name="RFID")),
                ("status", models.CharField(choices=[("scanned", "Scanned"), ("accepted", "Accepted"), ("rejected", "Rejected")], default="scanned", max_length=16)),
                ("authenticated", models.BooleanField(blank=True, null=True)),
                ("allowed", models.BooleanField(blank=True, null=True)),
                ("source", models.CharField(choices=[("service", "Scanner service"), ("browser", "Browser submission"), ("camera", "Camera scan"), ("on-demand", "On-demand scan"), ("ocpp", "OCPP")], max_length=32)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("attempted_at", models.DateTimeField(auto_now_add=True)),
                ("account", models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name="rfid_attempts", to="energy.customeraccount")),
                ("charger", models.ForeignKey(blank=True, null=True, on_delete=models.CASCADE, related_name="rfid_attempts", to="ocpp.charger")),
                ("label", models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name="attempts", to="cards.rfid")),
                ("transaction", models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name="rfid_attempts", to="ocpp.transaction")),
            ],
            options={
                "verbose_name": "RFID Attempt",
                "verbose_name_plural": "RFID Attempts",
                "ordering": ["-attempted_at"],
            },
        ),
        migrations.RunPython(copy_ocpp_attempts, noop),
    ]
