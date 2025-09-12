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


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0006_securitygroup_parent"),
        ("ocpp", "0002_initial"),
    ]

    operations = [
        migrations.RunPython(ensure_connector_id, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="charger",
            name="charger_id",
            field=models.CharField(
                max_length=100,
                verbose_name="Serial Number",
                help_text="Unique identifier reported by the charger.",
            ),
        ),
        migrations.AddConstraint(
            model_name="charger",
            constraint=models.UniqueConstraint(
                fields=("charger_id", "connector_id"),
                name="charger_connector_unique",
                nulls_distinct=False,
            ),
        ),
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
        migrations.CreateModel(
            name="MeterReading",
            fields=[],
            options={
                "verbose_name": "Meter Value",
                "verbose_name_plural": "Meter Values",
                "proxy": True,
                "indexes": [],
                "constraints": [],
            },
            bases=("ocpp.metervalue",),
        ),
    ]
