from django.db import migrations


def rename_account_rfids_column_forward(apps, schema_editor):
    connection = schema_editor.connection
    cursor = connection.cursor()
    table = "core_account_rfids"
    if table in connection.introspection.table_names():
        columns = {c.name for c in connection.introspection.get_table_description(cursor, table)}
        if "account_id" in columns and "energyaccount_id" not in columns:
            cursor.execute(
                'ALTER TABLE "core_account_rfids" RENAME COLUMN "account_id" TO "energyaccount_id"'
            )


def rename_account_rfids_column_backward(apps, schema_editor):
    connection = schema_editor.connection
    cursor = connection.cursor()
    table = "core_account_rfids"
    if table in connection.introspection.table_names():
        columns = {c.name for c in connection.introspection.get_table_description(cursor, table)}
        if "energyaccount_id" in columns and "account_id" not in columns:
            cursor.execute(
                'ALTER TABLE "core_account_rfids" RENAME COLUMN "energyaccount_id" TO "account_id"'
            )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0005_reference_transaction_uuid"),
    ]

    operations = [
        migrations.RunPython(
            rename_account_rfids_column_forward,
            rename_account_rfids_column_backward,
        ),
    ]
