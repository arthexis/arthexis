from django.db import migrations, models


def mark_existing_meter_evidence(apps, schema_editor):
    OcppTransaction = apps.get_model("ocpp", "OcppTransaction")
    OcppTransaction.objects.filter(
        meter_values__isnull=False,
    ).update(meter_evidence_revision=1)


def clear_existing_meter_evidence_revision(apps, schema_editor):
    OcppTransaction = apps.get_model("ocpp", "OcppTransaction")
    OcppTransaction.objects.update(meter_evidence_revision=0)


class Migration(migrations.Migration):
    dependencies = [
        ("ocpp", "0020_protocol_operation_attempt_identity"),
    ]

    operations = [
        migrations.AddField(
            model_name="ocpptransaction",
            name="meter_evidence_revision",
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="ocpptransaction",
            name="energy_derived_revision",
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.RunPython(
            mark_existing_meter_evidence,
            clear_existing_meter_evidence_revision,
        ),
    ]
