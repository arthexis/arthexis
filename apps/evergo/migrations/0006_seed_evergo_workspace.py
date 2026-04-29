from django.db import migrations


EVERGO_GROUP_NAME = "Evergo Contractors"
EVERGO_MODULE_MENU = "Evergo"
EVERGO_MODULE_PATH = "/evergo/"
EVERGO_WORKSPACE_PATH = "/evergo/workspace/"


def seed_evergo_workspace(apps, schema_editor):
    del schema_editor
    SecurityGroup = apps.get_model("groups", "SecurityGroup")
    Module = apps.get_model("modules", "Module")
    Landing = apps.get_model("pages", "Landing")

    group, _ = SecurityGroup.objects.get_or_create(name=EVERGO_GROUP_NAME)

    module, _ = Module.objects.get_or_create(
        path=EVERGO_MODULE_PATH,
        defaults={
            "menu": EVERGO_MODULE_MENU,
            "security_group": group,
            "security_mode": "exclusive",
        },
    )
    changed = []
    if module.menu != EVERGO_MODULE_MENU:
        module.menu = EVERGO_MODULE_MENU
        changed.append("menu")
    if module.security_group_id != group.id:
        module.security_group = group
        changed.append("security_group")
    if module.security_mode != "exclusive":
        module.security_mode = "exclusive"
        changed.append("security_mode")
    if changed:
        module.save(update_fields=changed)

    Landing.objects.get_or_create(
        module=module,
        path=EVERGO_WORKSPACE_PATH,
        defaults={"label": EVERGO_MODULE_MENU, "enabled": True},
    )


def unseed_evergo_workspace(apps, schema_editor):
    del schema_editor
    Module = apps.get_model("modules", "Module")
    Landing = apps.get_model("pages", "Landing")

    module = Module.objects.filter(path=EVERGO_MODULE_PATH).first()
    if module is None:
        return
    Landing.objects.filter(module=module, path=EVERGO_WORKSPACE_PATH).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("evergo", "0005_evergocustomersharelink"),
        ("groups", "0003_seed_ap_user_group"),
        ("modules", "0003_rename_docs_module_pill"),
        ("pages", "0007_seed_visitors_module"),
    ]

    operations = [
        migrations.RunPython(seed_evergo_workspace, unseed_evergo_workspace),
    ]
