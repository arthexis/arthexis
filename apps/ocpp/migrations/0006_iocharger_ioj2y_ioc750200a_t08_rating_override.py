from django.db import migrations
from django.db.models.functions import Lower, Trim


TARGET_VENDOR = "iocharger"
TARGET_FAMILY = "ioj2y"
TARGET_MODEL = "ioc750200a-t08"


def _matching_queryset(station_model):
    return station_model.objects.annotate(
        vendor_norm=Lower(Trim("vendor")),
        family_norm=Lower(Trim("model_family")),
        model_norm=Lower(Trim("model")),
    ).filter(
        vendor_norm=TARGET_VENDOR,
        family_norm=TARGET_FAMILY,
        model_norm=TARGET_MODEL,
    )


def set_iocharger_model_rating(apps, schema_editor):
    station_model = apps.get_model("ocpp", "StationModel")
    matched = _matching_queryset(station_model)
    if matched.exists():
        matched.update(integration_rating=4)
        return
    station_model.objects.create(
        vendor="IOCHARGER",
        model_family="IOJ2Y",
        model="IOC750200A-T08",
        integration_rating=4,
    )


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
