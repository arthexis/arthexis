"""Archive retired wiki bridge data outside the removed app."""

from django.db import migrations


ARCHIVE_TABLE = "legacy_wikis_wikimedia_bridge_archive"
SOURCE_TABLE = "wikis_wikimedia_bridge"


def archive_wiki_bridge_table(apps, schema_editor) -> None:
    """Rename the retired wiki bridge table into a legacy archive when present."""

    del apps
    existing_tables = set(schema_editor.connection.introspection.table_names())
    if ARCHIVE_TABLE in existing_tables:
        schema_editor.execute(f"DROP TABLE {ARCHIVE_TABLE}")
        existing_tables.remove(ARCHIVE_TABLE)

    if SOURCE_TABLE in existing_tables:
        schema_editor.execute(f"ALTER TABLE {SOURCE_TABLE} RENAME TO {ARCHIVE_TABLE}")


def restore_wiki_bridge_table(apps, schema_editor) -> None:
    """Restore the archived wiki bridge table name on rollback when available."""

    del apps
    existing_tables = set(schema_editor.connection.introspection.table_names())
    if ARCHIVE_TABLE in existing_tables and SOURCE_TABLE not in existing_tables:
        schema_editor.execute(f"ALTER TABLE {ARCHIVE_TABLE} RENAME TO {SOURCE_TABLE}")


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0009_application_enabled"),
    ]

    operations = [
        migrations.RunPython(
            archive_wiki_bridge_table,
            reverse_code=restore_wiki_bridge_table,
        ),
    ]
