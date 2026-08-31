from django.db import migrations


def _column_names(connection, table_name):
    with connection.cursor() as cursor:
        table_names = connection.introspection.table_names(cursor)
        if table_name not in table_names:
            return set()
        return {
            column.name
            for column in connection.introspection.get_table_description(
                cursor, table_name
            )
        }


def drop_stale_landing_track_leads(apps, schema_editor):
    table_name = "pages_landing"
    column_name = "track_leads"
    connection = schema_editor.connection
    if column_name not in _column_names(connection, table_name):
        return

    quote_name = connection.ops.quote_name
    schema_editor.execute(
        f"ALTER TABLE {quote_name(table_name)} DROP COLUMN {quote_name(column_name)}"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0002_initial"),
    ]

    operations = [
        migrations.RunPython(
            drop_stale_landing_track_leads,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
