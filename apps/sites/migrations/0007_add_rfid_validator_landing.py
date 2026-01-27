from django.db import migrations


def add_rfid_validator_landing(apps, schema_editor):
    Module = apps.get_model("modules", "Module")
    Landing = apps.get_model("pages", "Landing")

    module = Module.objects.filter(path="/ocpp/").first()
    if not module:
        return

    Landing.objects.get_or_create(
        module=module,
        path="/ocpp/rfid/validator/",
        defaults={
            "label": "RFID Card Validator",
            "enabled": True,
            "track_leads": False,
            "description": "",
            "is_seed_data": True,
        },
    )


class Migration(migrations.Migration):
    dependencies = [
        ("pages", "0006_sitebadge_favicon_media"),
        ("modules", "0005_module_features"),
    ]

    operations = [
        migrations.RunPython(add_rfid_validator_landing, migrations.RunPython.noop),
    ]
