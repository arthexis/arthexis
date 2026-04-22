from django.db import migrations


OCPP_MODULE_PATH = "/ocpp/"
OBSOLETE_LANDING_PATHS = (
    "/ocpp/charging-map/",
    "/ocpp/rfid/validator/",
)
REQUIRED_LANDINGS = (
    ("/ocpp/cpms/dashboard/", "Charging Station Dashboards"),
    ("/ocpp/evcs/simulator/", "EVCS Online Simulator"),
    ("/ocpp/charge-point-models/", "Supported CP Models"),
)


def sync_ocpp_terminal_landings(apps, schema_editor):
    del schema_editor
    Landing = apps.get_model("pages", "Landing")
    Module = apps.get_model("modules", "Module")

    module = Module.objects.filter(path=OCPP_MODULE_PATH).first()
    if module is None:
        return

    Landing.objects.filter(module=module, path__in=OBSOLETE_LANDING_PATHS).delete()

    for path, label in REQUIRED_LANDINGS:
        landing, created = Landing.objects.get_or_create(
            module=module,
            path=path,
            defaults={
                "enabled": True,
                "label": label,
            },
        )
        if created:
            continue
        changed_fields = []
        if landing.label != label:
            landing.label = label
            changed_fields.append("label")
        if not landing.enabled:
            landing.enabled = True
            changed_fields.append("enabled")
        if changed_fields:
            landing.save(update_fields=changed_fields)


class Migration(migrations.Migration):

    dependencies = [
        ("modules", "0003_rename_docs_module_pill"),
        ("pages", "0007_seed_visitors_module"),
    ]

    operations = [
        migrations.RunPython(sync_ocpp_terminal_landings, migrations.RunPython.noop),
    ]
