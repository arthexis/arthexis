"""Backfill public connector pages for existing chargers."""

from django.db import migrations



def backfill_public_connector_pages(apps, schema_editor):
    """Ensure every charger has a public connector page row."""

    del schema_editor
    Charger = apps.get_model("ocpp", "Charger")
    PublicConnectorPage = apps.get_model("ocpp", "PublicConnectorPage")

    existing = set(
        PublicConnectorPage.objects.values_list("charger_id", flat=True)
    )
    missing_ids = (
        Charger.objects.exclude(pk__in=existing).values_list("pk", flat=True)
    )
    PublicConnectorPage.objects.bulk_create(
        [PublicConnectorPage(charger_id=charger_id) for charger_id in missing_ids],
        ignore_conflicts=True,
    )



def rollback_public_connector_pages(apps, schema_editor):
    """No-op rollback for seeded pages."""

    del apps, schema_editor


class Migration(migrations.Migration):

    dependencies = [
        ("ocpp", "0028_googlemapslocation_location"),
    ]

    operations = [
        migrations.RunPython(
            backfill_public_connector_pages,
            reverse_code=rollback_public_connector_pages,
        ),
    ]
