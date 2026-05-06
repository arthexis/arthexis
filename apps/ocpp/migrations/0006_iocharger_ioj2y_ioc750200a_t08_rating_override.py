from django.db import migrations

TARGET_MODEL_LOOKUP = {
    "vendor": "IOCHARGER",
    "model_family": "IOJ2Y",
    "model": "IOC750200A-T08",
}


def _matching_queryset(station_model):
    return station_model.objects.filter(**TARGET_MODEL_LOOKUP)


def set_iocharger_model_rating(apps, schema_editor):
    station_model = apps.get_model("ocpp", "StationModel")
    _matching_queryset(station_model).update(integration_rating=4)


def unset_iocharger_model_rating(apps, schema_editor):
    station_model = apps.get_model("ocpp", "StationModel")
    _matching_queryset(station_model).update(integration_rating=5)


class Migration(migrations.Migration):

    dependencies = [
        ("ocpp", "0005_alter_simulator_options"),
    ]

    operations = [
        migrations.RunPython(
            set_iocharger_model_rating,
            reverse_code=unset_iocharger_model_rating,
        ),
    ]
