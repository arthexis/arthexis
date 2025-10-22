from django.db import migrations


AWG_PATH = "/awg/"
AWG_LANDING_PATH = "/awg/"
OLD_LABEL = "AWG Calculator"
NEW_LABEL = "AWG Cable Calculator"


def _manager(model, name):
    manager = getattr(model, name, None)
    if manager is not None:
        return manager
    return model.objects


def _set_awg_label(apps, label):
    Module = apps.get_model("pages", "Module")
    Landing = apps.get_model("pages", "Landing")

    module_manager = _manager(Module, "all_objects")
    landing_manager = _manager(Landing, "all_objects")

    modules = module_manager.filter(path=AWG_PATH)
    for module in modules:
        landings = landing_manager.filter(module=module, path=AWG_LANDING_PATH)
        for landing in landings:
            updates = []
            if landing.label != label:
                landing.label = label
                updates.append("label")
            if landing.description:
                landing.description = ""
                updates.append("description")
            if not landing.enabled:
                landing.enabled = True
                updates.append("enabled")
            if getattr(landing, "is_deleted", False):
                landing.is_deleted = False
                updates.append("is_deleted")
            if not getattr(landing, "is_seed_data", False):
                landing.is_seed_data = True
                updates.append("is_seed_data")
            if updates:
                landing.save(update_fields=updates)


def update_awg_label(apps, schema_editor):
    _set_awg_label(apps, NEW_LABEL)


def revert_awg_label(apps, schema_editor):
    _set_awg_label(apps, OLD_LABEL)


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0025_migrate_reader_module_paths"),
    ]

    operations = [
        migrations.RunPython(update_awg_label, revert_awg_label),
    ]
