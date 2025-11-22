from __future__ import annotations

from django.db import migrations


LANDING_PATH = "/ocpp/net-monitor/"
LANDING_LABEL = "Net Monitor Console"


def _manager(model):
    return getattr(model, "all_objects", model.objects)


def create_net_monitor_landing(apps, schema_editor):
    Application = apps.get_model("pages", "Application")
    Module = apps.get_model("pages", "Module")
    Landing = apps.get_model("pages", "Landing")

    application_manager = _manager(Application)
    module_manager = _manager(Module)
    landing_manager = _manager(Landing)

    ocpp_apps = application_manager.filter(name="ocpp")
    if not ocpp_apps.exists():
        return

    modules = module_manager.filter(application__in=ocpp_apps)
    for module in modules:
        defaults = {
            "label": LANDING_LABEL,
            "enabled": True,
            "description": "",
            "track_leads": False,
            "is_seed_data": getattr(module, "is_seed_data", False),
            "is_user_data": getattr(module, "is_user_data", False),
            "is_deleted": False,
        }
        landing, created = landing_manager.get_or_create(
            module=module,
            path=LANDING_PATH,
            defaults=defaults,
        )
        if created:
            continue
        updates: list[str] = []
        for field, expected in defaults.items():
            if getattr(landing, field) != expected:
                setattr(landing, field, expected)
                updates.append(field)
        if updates:
            landing.save(update_fields=updates)


def remove_net_monitor_landing(apps, schema_editor):
    Landing = apps.get_model("pages", "Landing")
    landing_manager = _manager(Landing)
    landing_manager.filter(
        module__application__name="ocpp", path=LANDING_PATH
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0039_remove_release_seed_data"),
    ]

    operations = [
        migrations.RunPython(create_net_monitor_landing, remove_net_monitor_landing),
    ]
