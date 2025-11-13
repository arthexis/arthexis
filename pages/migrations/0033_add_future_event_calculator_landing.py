from django.db import migrations


LANDING_PATH = "/awg/future-event/"
LANDING_LABEL = "Future Event Calculator"


def _manager(model):
    return getattr(model, "all_objects", model.objects)


def create_future_event_landing(apps, schema_editor):
    Application = apps.get_model("pages", "Application")
    Module = apps.get_model("pages", "Module")
    Landing = apps.get_model("pages", "Landing")

    application_manager = _manager(Application)
    module_manager = _manager(Module)
    landing_manager = _manager(Landing)

    awg_apps = application_manager.filter(name="awg")
    if not awg_apps.exists():
        return

    modules = module_manager.filter(application__in=awg_apps)
    for module in modules:
        defaults = {
            "label": LANDING_LABEL,
            "enabled": True,
            "track_leads": False,
            "is_seed_data": module.is_seed_data,
            "is_user_data": module.is_user_data,
            "is_deleted": False,
        }
        landing, created = landing_manager.get_or_create(
            module=module,
            path=LANDING_PATH,
            defaults=defaults,
        )
        if created:
            continue
        updates = []
        for field, expected in defaults.items():
            if getattr(landing, field) != expected:
                setattr(landing, field, expected)
                updates.append(field)
        if updates:
            landing.save(update_fields=updates)


def remove_future_event_landing(apps, schema_editor):
    Landing = apps.get_model("pages", "Landing")
    landing_manager = _manager(Landing)
    landing_manager.filter(
        module__application__name="awg",
        path=LANDING_PATH,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0032_alter_landinglead_ip_address_and_more"),
    ]

    operations = [
        migrations.RunPython(create_future_event_landing, remove_future_event_landing),
    ]
