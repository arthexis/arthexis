"""Archive retired wiki bridge data outside the removed app."""

from django.db import migrations


ARCHIVE_TABLE = "legacy_wikis_wikimedia_bridge_archive"
SOURCE_TABLE = "wikis_wikimedia_bridge"


def archive_wiki_bridge_table(apps, schema_editor) -> None:
    """Rename the retired wiki bridge table into a legacy archive when present."""

    del apps
    existing_tables = set(schema_editor.connection.introspection.table_names())
    quoted_archive_table = schema_editor.quote_name(ARCHIVE_TABLE)
    quoted_source_table = schema_editor.quote_name(SOURCE_TABLE)
    if ARCHIVE_TABLE in existing_tables:
        schema_editor.execute(f"DROP TABLE {quoted_archive_table}")
        existing_tables.remove(ARCHIVE_TABLE)

    if SOURCE_TABLE in existing_tables:
        schema_editor.execute(
            f"ALTER TABLE {quoted_source_table} RENAME TO {quoted_archive_table}"
        )


def restore_wiki_bridge_table(apps, schema_editor) -> None:
    """Restore the archived wiki bridge table name on rollback when available."""

    del apps
    existing_tables = set(schema_editor.connection.introspection.table_names())
    quoted_archive_table = schema_editor.quote_name(ARCHIVE_TABLE)
    quoted_source_table = schema_editor.quote_name(SOURCE_TABLE)
    if ARCHIVE_TABLE in existing_tables and SOURCE_TABLE not in existing_tables:
        schema_editor.execute(
            f"ALTER TABLE {quoted_archive_table} RENAME TO {quoted_source_table}"
        )


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
