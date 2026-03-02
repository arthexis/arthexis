from __future__ import annotations

from django.db import migrations


REQUIRED_TRANSACTION_COLUMNS: tuple[str, ...] = (
    "stop_reason",
    "authorization_status",
    "authorization_reason",
    "rejected_at",
)


def repair_transaction_columns(apps, schema_editor) -> None:
    """Add transaction columns when migration history drift leaves them absent."""

    transaction_model = apps.get_model("ocpp", "Transaction")
    table_name = transaction_model._meta.db_table

    with schema_editor.connection.cursor() as cursor:
        table_description = schema_editor.connection.introspection.get_table_description(
            cursor,
            table_name,
        )

    existing_columns = {column.name for column in table_description}

    for column_name in REQUIRED_TRANSACTION_COLUMNS:
        if column_name in existing_columns:
            continue
        schema_editor.add_field(
            transaction_model,
            transaction_model._meta.get_field(column_name),
        )


def noop_reverse(apps, schema_editor) -> None:
    """Keep repaired columns in place when this migration is unapplied."""


class Migration(migrations.Migration):

    dependencies = [
        ("ocpp", "0022_transaction_authorization_reason_and_more"),
    ]

    operations = [
        migrations.RunPython(repair_transaction_columns, reverse_code=noop_reverse),
    ]
