from django.db import migrations


AP_USER_GROUP_NAME = "AP User"
VISITORS_MODULE_MENU = "VISITORS"
VISITORS_MODULE_PATH = "/visitors/"
VISITORS_LANDING_LABEL = "Access Point Visitors"


def seed_visitors_module(apps, schema_editor):
    del schema_editor
    SecurityGroup = apps.get_model("groups", "SecurityGroup")
    Module = apps.get_model("modules", "Module")
    Landing = apps.get_model("pages", "Landing")

    group, _ = SecurityGroup.objects.get_or_create(name=AP_USER_GROUP_NAME)

    module, _ = Module.objects.get_or_create(
        path=VISITORS_MODULE_PATH,
        defaults={
            "menu": VISITORS_MODULE_MENU,
            "security_group": group,
            "security_mode": "exclusive",
        },
    )

    updates = {}
    if module.menu != VISITORS_MODULE_MENU:
        updates["menu"] = VISITORS_MODULE_MENU
    if module.security_group_id != group.id:
        updates["security_group"] = group
    if module.security_mode != "exclusive":
        updates["security_mode"] = "exclusive"
    if updates:
        for field, value in updates.items():
            setattr(module, field, value)
        module.save(update_fields=list(updates))

    Landing.objects.get_or_create(
        module=module,
        path=VISITORS_MODULE_PATH,
        defaults={
            "label": VISITORS_LANDING_LABEL,
            "enabled": True,
        },
    )


def unseed_visitors_module(apps, schema_editor):
    del schema_editor
    Module = apps.get_model("modules", "Module")
    Landing = apps.get_model("pages", "Landing")

    module = Module.objects.filter(path=VISITORS_MODULE_PATH).first()
    if module is None:
        return
    Landing.objects.filter(module=module, path=VISITORS_MODULE_PATH).delete()
    if module.menu == VISITORS_MODULE_MENU:
        module.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("groups", "0003_seed_ap_user_group"),
        ("modules", "0003_rename_docs_module_pill"),
        ("pages", "0006_merge_20260409_0001"),
    ]

    operations = [
        migrations.RunPython(seed_visitors_module, unseed_visitors_module),
    ]
