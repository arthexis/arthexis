"""Archive legacy Fitbit tables so the app can be removed from runtime."""

from __future__ import annotations

from django.db import migrations

TABLE_RENAMES = {
    "fitbit_fitbitconnection": "fitbit_archived_fitbitconnection",
    "fitbit_fitbithealthsample": "fitbit_archived_fitbithealthsample",
    "fitbit_fitbitnetmessagedelivery": "fitbit_archived_fitbitnetmessagedelivery",
}


def _rename_tables(*, schema_editor, forward: bool) -> None:
    """Rename legacy Fitbit tables while keeping rollback support."""

    cursor = schema_editor.connection.cursor()
    existing_tables = set(schema_editor.connection.introspection.table_names(cursor))
    for source, target in TABLE_RENAMES.items():
        old_name, new_name = (source, target) if forward else (target, source)
        if old_name not in existing_tables or new_name in existing_tables:
            continue
        schema_editor.alter_db_table(None, old_name, new_name)
        existing_tables.remove(old_name)
        existing_tables.add(new_name)


def archive_fitbit_tables(apps, schema_editor):
    """Move live Fitbit tables to archived names on upgrade."""

    del apps
    _rename_tables(schema_editor=schema_editor, forward=True)


def restore_fitbit_tables(apps, schema_editor):
    """Restore archived Fitbit tables when rolling this migration back."""

    del apps
    _rename_tables(schema_editor=schema_editor, forward=False)


class Migration(migrations.Migration):
    dependencies = [
        ("fitbit", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(archive_fitbit_tables, restore_fitbit_tables),
    ]
