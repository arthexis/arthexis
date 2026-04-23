from django.db import migrations


CARD_SHOP_PATH = "/shop/"
WATCHTOWER_ROLE_NAMES = ("Watchtower", "Constellation")


def limit_card_shop_module_to_watchtower(apps, schema_editor):
    Module = apps.get_model("modules", "Module")
    NodeRole = apps.get_model("nodes", "NodeRole")

    module = Module.objects.filter(path=CARD_SHOP_PATH).first()
    if module is None:
        return

    watchtower_roles = list(NodeRole.objects.filter(name__in=WATCHTOWER_ROLE_NAMES))
    if not watchtower_roles:
        return

    module.roles.set(watchtower_roles)


def restore_card_shop_module_terminal_role(apps, schema_editor):
    Module = apps.get_model("modules", "Module")
    NodeRole = apps.get_model("nodes", "NodeRole")

    module = Module.objects.filter(path=CARD_SHOP_PATH).first()
    if module is None:
        return

    terminal_roles = list(NodeRole.objects.filter(name="Terminal"))
    module.roles.set(terminal_roles)


class Migration(migrations.Migration):

    dependencies = [
        ("modules", "0003_rename_docs_module_pill"),
        ("nodes", "0010_cleanup_retired_node_feature_slugs"),
    ]

    operations = [
        migrations.RunPython(
            limit_card_shop_module_to_watchtower,
            restore_card_shop_module_terminal_role,
        ),
    ]
