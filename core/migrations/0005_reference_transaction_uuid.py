from django.db import migrations, models
import uuid

try:  # pragma: no cover - psycopg is only needed when Postgres is available
    import psycopg
except Exception:  # pragma: no cover
    psycopg = None


def add_transaction_uuid_field(apps, schema_editor):
    """Add ``transaction_uuid`` to ``core.Reference`` if missing.

    Previous installations may already include the column, so this migration
    guards against attempting to add it twice. Duplicate-column errors are
    ignored so the migration remains idempotent across backends.
    """

    Reference = apps.get_model("core", "Reference")
    field = models.UUIDField(
        default=uuid.uuid4,
        editable=True,
        db_index=True,
        verbose_name="transaction UUID",
    )
    field.set_attributes_from_name("transaction_uuid")
    try:
        schema_editor.add_field(Reference, field)
    except Exception as exc:  # pragma: no cover - depends on database backend
        duplicate = False
        if psycopg and isinstance(
            getattr(exc, "__cause__", exc), psycopg.errors.DuplicateColumn
        ):
            duplicate = True
        elif "already exists" in str(exc).lower():
            duplicate = True
        if not duplicate:
            raise


def assign_transaction_uuids(apps, schema_editor):
    Reference = apps.get_model("core", "Reference")
    for ref in Reference.objects.filter(transaction_uuid__isnull=True):
        ref.transaction_uuid = uuid.uuid4()
        ref.save(update_fields=["transaction_uuid"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0004_userdatum"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    add_transaction_uuid_field, migrations.RunPython.noop
                )
            ],
            state_operations=[
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
            ],
        ),
        migrations.RunPython(assign_transaction_uuids, migrations.RunPython.noop),
    ]
