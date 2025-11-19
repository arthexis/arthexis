from django.db import migrations, models


def update_energy_units(apps, schema_editor):
    Charger = apps.get_model("ocpp", "Charger")
    Charger.objects.filter(energy_unit="kWh").update(energy_unit="kW")
    Charger.objects.filter(energy_unit="Wh").update(energy_unit="W")


def revert_energy_units(apps, schema_editor):
    Charger = apps.get_model("ocpp", "Charger")
    Charger.objects.filter(energy_unit="kW").update(energy_unit="kWh")
    Charger.objects.filter(energy_unit="W").update(energy_unit="Wh")


class Migration(migrations.Migration):

    dependencies = [
        ("ocpp", "0055_charger_energy_unit"),
    ]

    operations = [
        migrations.RunPython(update_energy_units, revert_energy_units),
        migrations.AlterField(
            model_name="charger",
            name="energy_unit",
            field=models.CharField(
                choices=[("kW", "kW"), ("W", "W")],
                default="kW",
                help_text="Energy unit expected from this charger.",
                max_length=4,
                verbose_name="Charger Units",
            ),
        ),
    ]
