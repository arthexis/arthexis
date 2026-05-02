from django.db import migrations

GALLERY_APPLICATION_NAME = "gallery"
GALLERY_APPLICATION_DESCRIPTION = "Public image galleries and guest art uploads."
GALLERY_MODULE_MENU = "Gallery"
GALLERY_MODULE_PATH = "/gallery/"
GALLERY_LANDING_LABEL = "Gallery"
GALLERY_LANDING_PATH = "/gallery/ap/"
GALLERY_ROLE_NAMES = ("Control", "Satellite", "Gateway")


def seed_gallery_module(apps, schema_editor):
    del schema_editor
    Application = apps.get_model("app", "Application")
    Module = apps.get_model("modules", "Module")
    NodeRole = apps.get_model("nodes", "NodeRole")
    Landing = apps.get_model("pages", "Landing")

    application, _ = Application.objects.get_or_create(
        name=GALLERY_APPLICATION_NAME,
        defaults={
            "description": GALLERY_APPLICATION_DESCRIPTION,
            "importance": "baseline",
            "is_seed_data": True,
        },
    )

    module, created = Module.objects.get_or_create(
        path=GALLERY_MODULE_PATH,
        defaults={
            "application": application,
            "menu": GALLERY_MODULE_MENU,
            "priority": 0,
            "is_default": False,
            "security_mode": "inclusive",
            "is_seed_data": True,
        },
    )
    if created:
        module.is_seed_data = True
        module.save(update_fields=["is_seed_data"])
    elif (
        module.application_id in {None, application.pk}
        and module.security_group_id is None
    ):
        changed_fields = []
        if module.application_id != application.pk:
            module.application = application
            changed_fields.append("application")
        if (
            module.menu in {"", GALLERY_MODULE_MENU}
            and module.menu != GALLERY_MODULE_MENU
        ):
            module.menu = GALLERY_MODULE_MENU
            changed_fields.append("menu")
        if module.security_mode != "inclusive":
            module.security_mode = "inclusive"
            changed_fields.append("security_mode")
        if changed_fields:
            module.save(update_fields=changed_fields)

    roles = list(NodeRole.objects.filter(name__in=GALLERY_ROLE_NAMES))
    if roles:
        module.roles.set(roles)

    landing, created = Landing.objects.get_or_create(
        module=module,
        path=GALLERY_LANDING_PATH,
        defaults={
            "label": GALLERY_LANDING_LABEL,
            "enabled": True,
            "track_leads": False,
            "description": "",
            "is_seed_data": True,
        },
    )
    if created:
        landing.is_seed_data = True
        landing.save(update_fields=["is_seed_data"])
        return

    changed_fields = []
    if landing.label != GALLERY_LANDING_LABEL:
        landing.label = GALLERY_LANDING_LABEL
        changed_fields.append("label")
    if not landing.enabled:
        landing.enabled = True
        changed_fields.append("enabled")
    if changed_fields:
        landing.save(update_fields=changed_fields)


def unseed_gallery_module(apps, schema_editor):
    del schema_editor
    Module = apps.get_model("modules", "Module")
    Landing = apps.get_model("pages", "Landing")

    module = Module.objects.filter(path=GALLERY_MODULE_PATH).first()
    if module is None:
        return
    Landing.objects.filter(module=module, path=GALLERY_LANDING_PATH).delete()
    if module.is_seed_data and not module.landings.exists():
        module.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0002_application_importance_legacy"),
        ("modules", "0004_limit_card_shop_module_to_watchtower"),
        ("nodes", "0010_cleanup_retired_node_feature_slugs"),
        ("pages", "0010_alter_sitehighlight_options_sitehighlight_updated_at"),
    ]

    operations = [
        migrations.RunPython(seed_gallery_module, unseed_gallery_module),
    ]
