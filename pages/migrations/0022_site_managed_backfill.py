from django.db import migrations


def ensure_managed_column(apps, schema_editor):
    connection = schema_editor.connection
    table_name = "django_site"

    with connection.cursor() as cursor:
        existing_columns = {
            info.name for info in connection.introspection.get_table_description(cursor, table_name)
        }

    if "managed" in existing_columns:
        return

    column_name = schema_editor.quote_name("managed")
    table_quoted = schema_editor.quote_name(table_name)

    if connection.vendor == "sqlite":
        column_definition = f"{column_name} INTEGER NOT NULL DEFAULT 0"
    else:
        column_definition = f"{column_name} BOOLEAN NOT NULL DEFAULT FALSE"

    schema_editor.execute(f"ALTER TABLE {table_quoted} ADD COLUMN {column_definition}")


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0021_site_managed_require_https"),
    ]

    operations = [
        migrations.RunPython(ensure_managed_column, migrations.RunPython.noop),
    ]

