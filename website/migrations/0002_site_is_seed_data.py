from django.db import connection, migrations


def add_is_seed_data(apps, schema_editor):
    with connection.cursor() as cursor:
        existing = {
            col.name
            for col in connection.introspection.get_table_description(
                cursor, "django_site"
            )
        }
    if "is_seed_data" not in existing:
        schema_editor.execute(
            "ALTER TABLE django_site ADD COLUMN is_seed_data BOOLEAN NOT NULL DEFAULT 0"
        )


def drop_is_seed_data(apps, schema_editor):
    with connection.cursor() as cursor:
        existing = {
            col.name
            for col in connection.introspection.get_table_description(
                cursor, "django_site"
            )
        }
    if "is_seed_data" in existing:
        try:
            schema_editor.execute(
                "ALTER TABLE django_site DROP COLUMN is_seed_data"
            )
        except Exception:
            # Some SQLite versions don't support dropping columns; ignore.
            pass


class Migration(migrations.Migration):

    dependencies = [
        ("website", "0001_initial"),
        ("sites", "0001_initial"),
    ]

    operations = [migrations.RunPython(add_is_seed_data, drop_is_seed_data)]

