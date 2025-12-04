from django.db import migrations


def purge_modules_and_landings(apps, schema_editor):
    Landing = apps.get_model("pages", "Landing")
    Module = apps.get_model("pages", "Module")

    Landing.objects.all().delete()
    Module.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0006_purge_module_and_landing_seed_data"),
    ]

    operations = [
        migrations.RunPython(purge_modules_and_landings, migrations.RunPython.noop),
    ]
