"""Seed the Card Shop module pill for Terminal nodes."""

from django.db import migrations

CARD_SHOP_MODULE_PATH = "/shop/"
CARD_SHOP_LANDING_PATH = "/shop/"
CARD_SHOP_MENU_LABEL = "Card Shop"
CARD_SHOP_LANDING_LABEL = "RFID Card Shop"
TERMINAL_ROLE_NAME = "Terminal"
SHOP_APP_NAME = "shop"


def seed_card_shop_module(apps, schema_editor):
    """Create or update the seeded Card Shop module and landing for Terminal roles."""

    Module = apps.get_model("modules", "Module")
    Landing = apps.get_model("pages", "Landing")
    NodeRole = apps.get_model("nodes", "NodeRole")
    Application = apps.get_model("app", "Application")

    db_alias = schema_editor.connection.alias

    module, _ = Module.objects.using(db_alias).update_or_create(
        path=CARD_SHOP_MODULE_PATH,
        defaults={
            "is_seed_data": True,
            "is_deleted": False,
            "menu": CARD_SHOP_MENU_LABEL,
            "priority": 0,
            "is_default": False,
            "security_mode": "inclusive",
            "application": Application.objects.using(db_alias)
            .filter(name=SHOP_APP_NAME)
            .first(),
        },
    )

    terminal_role = NodeRole.objects.using(db_alias).filter(name=TERMINAL_ROLE_NAME).first()
    module.roles.clear()
    if terminal_role is not None:
        module.roles.add(terminal_role)

    Landing.objects.using(db_alias).update_or_create(
        module=module,
        path=CARD_SHOP_LANDING_PATH,
        defaults={
            "is_seed_data": True,
            "is_deleted": False,
            "label": CARD_SHOP_LANDING_LABEL,
            "enabled": True,
            "track_leads": False,
            "description": "",
        },
    )


def remove_card_shop_module(apps, schema_editor):
    """Remove the seeded Card Shop module and landing entries."""

    Module = apps.get_model("modules", "Module")
    Landing = apps.get_model("pages", "Landing")

    db_alias = schema_editor.connection.alias

    Landing.objects.using(db_alias).filter(
        module__path=CARD_SHOP_MODULE_PATH,
        path=CARD_SHOP_LANDING_PATH,
        is_seed_data=True,
    ).delete()
    Module.objects.using(db_alias).filter(
        path=CARD_SHOP_MODULE_PATH,
        is_seed_data=True,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("modules", "0008_deprecate_charge_points_module_feature"),
    ]

    operations = [
        migrations.RunPython(seed_card_shop_module, remove_card_shop_module),
    ]
