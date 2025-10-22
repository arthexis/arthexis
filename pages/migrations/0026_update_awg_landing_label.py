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


def _set_awg_label(apps, *, from_label, to_label):
    Module = apps.get_model("pages", "Module")
    Landing = apps.get_model("pages", "Landing")

    module_manager = _manager(Module, "all_objects")
    landing_manager = _manager(Landing, "all_objects")

    modules = module_manager.filter(path=AWG_PATH)
    for module in modules:
        landings = landing_manager.filter(
            module=module,
            path=AWG_LANDING_PATH,
            label=from_label,
        )
        for landing in landings:
            if landing.label != to_label:
                landing.label = to_label
                landing.save(update_fields=["label"])


def update_awg_label(apps, schema_editor):
    _set_awg_label(apps, from_label=OLD_LABEL, to_label=NEW_LABEL)


def revert_awg_label(apps, schema_editor):
    _set_awg_label(apps, from_label=NEW_LABEL, to_label=OLD_LABEL)


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0025_migrate_reader_module_paths"),
    ]

    operations = [
        migrations.RunPython(update_awg_label, revert_awg_label),
    ]
