from django.db import migrations

LOOKUP = {
    "vendor": "IOCHARGER",
    "model": "IOC750200A-T08",
}

OLD_VALUES = {
    "model_family": "IOJ2Y",
    "max_power_kw": "7.50",
    "max_voltage_v": 480,
    "connector_type": "CCS Type 1",
    "notes": "Factory 7.5kW single-port configuration for IOCHARGER IOJ2Y series.",
    "integration_rating": 4,
}

NEW_VALUES = {
    "model_family": "IOCJY2",
    "max_power_kw": "120.00",
    "max_voltage_v": 1000,
    "connector_type": "Dual CCS Type 2",
    "notes": "Updated to reflect field-validated dual-connector DC fast charger behavior for IOC750200A-T08.",
    "integration_rating": 5,
}


def apply_spec_correction(apps, schema_editor):
    station_model = apps.get_model("ocpp", "StationModel")
    station_model.objects.filter(
        **LOOKUP,
        model_family=OLD_VALUES["model_family"],
    ).update(**NEW_VALUES)


def revert_spec_correction(apps, schema_editor):
    station_model = apps.get_model("ocpp", "StationModel")
    station_model.objects.filter(
        **LOOKUP,
        model_family=NEW_VALUES["model_family"],
    ).update(**OLD_VALUES)


class Migration(migrations.Migration):

    dependencies = [
        ("ocpp", "0007_alter_cpforwarder_enabled"),
    ]

    operations = [
        migrations.RunPython(
            apply_spec_correction,
            reverse_code=revert_spec_correction,
        ),
    ]
