from django.db import migrations


def update_sigilroots(apps, schema_editor):
    SigilRoot = apps.get_model("core", "SigilRoot")
    ContentType = apps.get_model("contenttypes", "ContentType")

    updates = {
        "ECOLL": ("teams", "emailcollector"),
        "EV": ("ocpp", "electricvehicle"),
        "EVB": ("ocpp", "brand"),
        "EVM": ("ocpp", "evmodel"),
        "INBOX": ("teams", "emailinbox"),
        "OUTBOX": ("teams", "emailoutbox"),
        "LOC": ("core", "location"),
        "WMI": ("ocpp", "wmicode"),
    }

    for prefix, (app_label, model) in updates.items():
        try:
            content_type = ContentType.objects.get(
                app_label=app_label, model=model
            )
        except ContentType.DoesNotExist:
            continue

        SigilRoot.objects.filter(prefix=prefix).update(content_type=content_type)


def revert_sigilroots(apps, schema_editor):
    SigilRoot = apps.get_model("core", "SigilRoot")
    ContentType = apps.get_model("contenttypes", "ContentType")

    previous_mappings = {
        "ECOLL": ("core", "emailcollector"),
        "EV": ("core", "electricvehicle"),
        "EVB": ("core", "brand"),
        "EVM": ("core", "evmodel"),
        "INBOX": ("core", "emailinbox"),
        "OUTBOX": ("nodes", "emailoutbox"),
        "LOC": ("ocpp", "location"),
        "WMI": ("core", "wmicode"),
    }

    for prefix, (app_label, model) in previous_mappings.items():
        try:
            content_type = ContentType.objects.get(
                app_label=app_label, model=model
            )
        except ContentType.DoesNotExist:
            continue

        SigilRoot.objects.filter(prefix=prefix).update(content_type=content_type)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0100_alter_invitelead_ip_address_and_more"),
    ]

    operations = [
        migrations.RunPython(update_sigilroots, revert_sigilroots),
    ]

