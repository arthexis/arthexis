from django.db import migrations


def seed_public_connector_pages(apps, schema_editor):
    """Ensure each charge point has a public connector QR page record."""

    Charger = apps.get_model("ocpp", "Charger")
    PublicConnectorPage = apps.get_model("ocpp", "PublicConnectorPage")
    db_alias = schema_editor.connection.alias
    for charger_id in (
        Charger.objects.using(db_alias)
        .values_list("pk", flat=True)
        .iterator()
    ):
        PublicConnectorPage.objects.using(db_alias).get_or_create(charger_id=charger_id)


def unseed_public_connector_pages(apps, schema_editor):
    """No-op reverse migration to avoid deleting operator-configured pages."""


class Migration(migrations.Migration):
    dependencies = [
        ("ocpp", "0028_googlemapslocation_location"),
    ]

    operations = [
        migrations.RunPython(
            seed_public_connector_pages,
            reverse_code=unseed_public_connector_pages,
        ),
    ]
