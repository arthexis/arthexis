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
        field = models.CharField(max_length=10, null=True, blank=True)
        field.set_attributes_from_name("connector_id")
        schema_editor.add_field(Charger, field)
    if "display_name" not in columns:
        field = models.CharField(max_length=200, null=True, blank=True)
        field.set_attributes_from_name("display_name")
        schema_editor.add_field(Charger, field)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0006_securitygroup_parent"),
        ("ocpp", "0002_initial"),
    ]

    operations = [
        migrations.RunPython(ensure_connector_id, migrations.RunPython.noop),
        migrations.CreateModel(
            name="ElectricVehicle",
            fields=[],
            options={
                "verbose_name": "Electric Vehicle",
                "verbose_name_plural": "Electric Vehicles",
                "proxy": True,
                "indexes": [],
                "constraints": [],
            },
            bases=("core.electricvehicle",),
        ),
    ]
