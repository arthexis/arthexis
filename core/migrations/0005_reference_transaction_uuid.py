from django.db import migrations, models
import uuid


def assign_transaction_uuids(apps, schema_editor):
    Reference = apps.get_model("core", "Reference")
    for ref in Reference.objects.all():
        ref.transaction_uuid = uuid.uuid4()
        ref.save(update_fields=["transaction_uuid"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0004_userdatum"),
    ]

    operations = [
        migrations.AddField(
            model_name="reference",
            name="transaction_uuid",
            field=models.UUIDField(
                default=uuid.uuid4,
                editable=True,
                db_index=True,
                verbose_name="transaction UUID",
            ),
        ),
        migrations.RunPython(assign_transaction_uuids, migrations.RunPython.noop),
    ]
