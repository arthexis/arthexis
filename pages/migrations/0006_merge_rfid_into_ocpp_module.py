from django.db import migrations


RFID_PATH = "/ocpp/rfid/"
OCPP_PATH = "/ocpp/"
RFID_LABEL = "Identity Validator"


def _manager(model, name):
    manager = getattr(model, name, None)
    if manager is not None:
        return manager
    return model.objects


def merge_rfid_into_ocpp(apps, schema_editor):
    Module = apps.get_model("pages", "Module")
    Landing = apps.get_model("pages", "Landing")

    module_manager = _manager(Module, "all_objects")
    landing_manager = _manager(Landing, "all_objects")

    ocpp_modules = list(module_manager.filter(path=OCPP_PATH))
    for ocpp_module in ocpp_modules:
        landing, created = landing_manager.get_or_create(
            module=ocpp_module,
            path=RFID_PATH,
            defaults={"label": RFID_LABEL, "enabled": True, "description": ""},
        )

        updated_fields = []
        if landing.label != RFID_LABEL:
            landing.label = RFID_LABEL
            updated_fields.append("label")
        if landing.is_deleted:
            landing.is_deleted = False
            updated_fields.append("is_deleted")
        if not landing.enabled:
            landing.enabled = True
            updated_fields.append("enabled")
        if created and not landing.is_seed_data:
            landing.is_seed_data = True
            updated_fields.append("is_seed_data")

        if updated_fields:
            landing.save(update_fields=updated_fields)

    rfid_module_ids = list(
        module_manager.filter(path=RFID_PATH).values_list("id", flat=True)
    )
    if rfid_module_ids:
        module_manager.filter(id__in=rfid_module_ids).update(is_deleted=True)
        landing_manager.filter(module_id__in=rfid_module_ids).update(is_deleted=True)


def restore_rfid_module(apps, schema_editor):
    Module = apps.get_model("pages", "Module")
    Landing = apps.get_model("pages", "Landing")

    module_manager = _manager(Module, "all_objects")
    landing_manager = _manager(Landing, "all_objects")

    rfid_module_ids = list(
        module_manager.filter(path=RFID_PATH).values_list("id", flat=True)
    )
    if rfid_module_ids:
        module_manager.filter(id__in=rfid_module_ids).update(is_deleted=False)
        landing_manager.filter(module_id__in=rfid_module_ids).update(is_deleted=False)

    ocpp_modules = module_manager.filter(path=OCPP_PATH)
    for ocpp_module in ocpp_modules:
        landing_qs = landing_manager.filter(module=ocpp_module, path=RFID_PATH)
        for landing in landing_qs:
            if not landing.is_deleted:
                landing.is_deleted = True
                landing.save(update_fields=["is_deleted"])


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0005_hide_constellation_rfid"),
    ]

    operations = [
        migrations.RunPython(merge_rfid_into_ocpp, restore_rfid_module),
    ]
