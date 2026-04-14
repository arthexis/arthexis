from django.db import migrations


def rename_docs_module_pill(apps, schema_editor):
    del schema_editor
    Module = apps.get_model("modules", "Module")
    Module.objects.filter(path="/docs/", menu="Docs").update(menu="Developers")


def revert_docs_module_pill(apps, schema_editor):
    del schema_editor
    Module = apps.get_model("modules", "Module")
    Module.objects.filter(path="/docs/", menu="Developers").update(menu="Docs")


class Migration(migrations.Migration):
    dependencies = [
        ("modules", "0002_initial"),
    ]

    operations = [
        migrations.RunPython(rename_docs_module_pill, revert_docs_module_pill),
    ]
