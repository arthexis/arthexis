from django.db import migrations, models


def ensure_connector_id(apps, schema_editor):
    Charger = apps.get_model("ocpp", "Charger")
    table = Charger._meta.db_table
    with schema_editor.connection.cursor() as cursor:
        columns = [
            col.name
            for col in schema_editor.connection.introspection.get_table_description(
                cursor, table
            )
        ]
    if "connector_id" not in columns:
        field = models.PositiveIntegerField(default=1)
        field.set_attributes_from_name("connector_id")
        schema_editor.add_field(Charger, field)


class Migration(migrations.Migration):
    dependencies = [
        ("ocpp", "0002_initial"),
    ]

    operations = [
        migrations.RunPython(ensure_connector_id, migrations.RunPython.noop),
    ]
