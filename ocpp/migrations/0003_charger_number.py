from django.db import migrations, models


def ensure_charger_number(apps, schema_editor):
    Charger = apps.get_model("ocpp", "Charger")
    table = Charger._meta.db_table
    with schema_editor.connection.cursor() as cursor:
        columns = [
            col.name
            for col in schema_editor.connection.introspection.get_table_description(
                cursor, table
            )
        ]
    if "number" not in columns:
        field = models.PositiveIntegerField(default=1)
        field.set_attributes_from_name("number")
        schema_editor.add_field(Charger, field)


class Migration(migrations.Migration):
    dependencies = [
        ("ocpp", "0002_initial"),
    ]

    operations = [
        migrations.RunPython(ensure_charger_number, migrations.RunPython.noop),
    ]
