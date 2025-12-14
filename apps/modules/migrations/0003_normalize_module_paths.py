from django.db import migrations


def normalize(path):
    if path is None:
        return None
    stripped = str(path).strip("/")
    return "/" if stripped == "" else f"/{stripped}/"


def forwards(apps, schema_editor):
    Module = apps.get_model("modules", "Module")
    for module in Module.objects.all():
        normalized = normalize(module.path)
        if normalized != module.path:
            module.path = normalized
            module.save(update_fields=["path"])


class Migration(migrations.Migration):

    dependencies = [
        ("modules", "0002_initial"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
