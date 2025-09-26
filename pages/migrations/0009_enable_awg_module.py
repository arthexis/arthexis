from django.db import migrations

AWG_PATH = "/awg/"
AWG_LANDINGS = (
    ("/awg/", "AWG Calculator"),
    ("/awg/energy-tariff/", "Energy Tariff Calculator"),
)


def _manager(model, name):
    manager = getattr(model, name, None)
    if manager is not None:
        return manager
    return model.objects


def enable_awg_module(apps, schema_editor):
    Application = apps.get_model("pages", "Application")
    Module = apps.get_model("pages", "Module")
    Landing = apps.get_model("pages", "Landing")
    NodeRole = apps.get_model("nodes", "NodeRole")

    application_manager = _manager(Application, "all_objects")
    module_manager = _manager(Module, "all_objects")
    landing_manager = _manager(Landing, "all_objects")
    role_manager = _manager(NodeRole, "all_objects")

    try:
        awg_app = application_manager.get(name="awg")
    except Application.DoesNotExist:
        return

    for role in role_manager.all():
        module, _ = module_manager.update_or_create(
            node_role=role,
            path=AWG_PATH,
            defaults={
                "application": awg_app,
                "is_seed_data": True,
                "is_deleted": False,
            },
        )

        updated_fields = []
        if module.application_id != awg_app.id:
            module.application = awg_app
            updated_fields.append("application")
        if module.is_deleted:
            module.is_deleted = False
            updated_fields.append("is_deleted")
        if not module.is_seed_data:
            module.is_seed_data = True
            updated_fields.append("is_seed_data")
        if updated_fields:
            module.save(update_fields=updated_fields)

        for path, label in AWG_LANDINGS:
            landing, _ = landing_manager.get_or_create(
                module=module,
                path=path,
                defaults={
                    "label": label,
                    "description": "",
                    "enabled": True,
                },
            )

            landing_updates = []
            if landing.label != label:
                landing.label = label
                landing_updates.append("label")
            if landing.description != "":
                landing.description = ""
                landing_updates.append("description")
            if not landing.enabled:
                landing.enabled = True
                landing_updates.append("enabled")
            if landing.is_deleted:
                landing.is_deleted = False
                landing_updates.append("is_deleted")
            if not landing.is_seed_data:
                landing.is_seed_data = True
                landing_updates.append("is_seed_data")
            if landing_updates:
                landing.save(update_fields=landing_updates)


def noop(apps, schema_editor):
    """No-op reverse migration."""


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0008_arthexis_favicon"),
    ]

    operations = [
        migrations.RunPython(enable_awg_module, noop),
    ]
