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

    module = Module.objects.filter(path=VISITORS_MODULE_PATH).first()
    if module is None:
        module = Module.objects.create(
            path=VISITORS_MODULE_PATH,
            menu=VISITORS_MODULE_MENU,
            security_group=group,
            security_mode="exclusive",
        )
    elif (
        module.menu != VISITORS_MODULE_MENU
        or module.security_group_id != group.id
        or module.security_mode != "exclusive"
    ):
        return

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
    if (
        module is None
        or module.menu != VISITORS_MODULE_MENU
        or module.security_mode != "exclusive"
    ):
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
