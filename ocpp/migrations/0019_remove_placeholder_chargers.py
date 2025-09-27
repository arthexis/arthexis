from django.db import migrations


def remove_placeholder_chargers(apps, schema_editor):
    Charger = apps.get_model("ocpp", "Charger")
    Location = apps.get_model("ocpp", "Location")

    placeholder_ids = list(
        Charger.objects.filter(
            charger_id__startswith="<", charger_id__endswith=">"
        ).values_list("pk", flat=True)
    )
    if placeholder_ids:
        Charger.objects.filter(pk__in=placeholder_ids).delete()
    Location.objects.filter(
        name__startswith="<", name__endswith=">", chargers__isnull=True
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("ocpp", "0018_charger_availability_request_details_and_more"),
    ]

    operations = [
        migrations.RunPython(remove_placeholder_chargers, migrations.RunPython.noop),
    ]
