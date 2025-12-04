from __future__ import annotations

from django.db import migrations


def purge_module_and_landing_seed_data(apps, schema_editor):
    Landing = apps.get_model("pages", "Landing")
    Module = apps.get_model("pages", "Module")

    Landing.objects.filter(is_seed_data=True).delete()
    Module.objects.filter(is_seed_data=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0005_remove_invalid_application_landings"),
    ]

    operations = [
        migrations.RunPython(
            purge_module_and_landing_seed_data, migrations.RunPython.noop
        ),
    ]
