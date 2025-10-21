from django.db import migrations, models
from django.db.models import Q


def copy_vin_to_vid(apps, schema_editor):
    Transaction = apps.get_model("ocpp", "Transaction")
    updates = []
    queryset = (
        Transaction.objects.filter(Q(vid__isnull=True) | Q(vid=""))
        .exclude(Q(vin__isnull=True) | Q(vin=""))
        .iterator()
    )
    for tx in queryset:
        identifier = (tx.vin or "").strip()
        if not identifier:
            continue
        tx.vid = identifier
        updates.append(tx)
        if len(updates) >= 500:
            Transaction.objects.bulk_update(updates, ["vid"])
            updates.clear()
    if updates:
        Transaction.objects.bulk_update(updates, ["vid"])


class Migration(migrations.Migration):

    dependencies = [
        ("ocpp", "0028_transaction_vid"),
    ]

    operations = [
        migrations.AlterField(
            model_name="transaction",
            name="vin",
            field=models.CharField(
                blank=True,
                help_text="Deprecated. Use VID instead.",
                max_length=17,
            ),
        ),
        migrations.RunPython(copy_vin_to_vid, migrations.RunPython.noop),
    ]
