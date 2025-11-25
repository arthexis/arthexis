from django.db import migrations
from django.db.models import Q


def rename_chargers_menu(apps, schema_editor):
    Module = apps.get_model("pages", "Module")

    Module.objects.filter(
        Q(menu__iexact="Chargers") | Q(menu__iexact="Charge Points"),
        path="/ocpp/",
    ).update(menu="Charge Points")


def restore_chargers_menu(apps, schema_editor):
    Module = apps.get_model("pages", "Module")

    Module.objects.filter(menu__iexact="Charge Points", path="/ocpp/").update(
        menu="Chargers"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0043_module_priority"),
    ]

    operations = [
        migrations.RunPython(rename_chargers_menu, restore_chargers_menu),
    ]
