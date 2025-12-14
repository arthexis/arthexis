from django.db import migrations


def normalize(path):
    if path is None:
        return None
    stripped = str(path).strip("/")
    return "/" if stripped == "" else f"/{stripped}/"


def forwards(apps, schema_editor):
    Module = apps.get_model("modules", "Module")
    taken_paths = set(Module.objects.values_list("path", flat=True))

    for module in Module.objects.order_by("id"):
        normalized = normalize(module.path)
        if normalized == module.path:
            continue

        # Avoid collisions when multiple rows normalize to the same path.
        taken_paths.discard(module.path)
        target = normalized
        if target in taken_paths:
            base = normalized.strip("/")
            counter = 0
            while target in taken_paths:
                suffix = f"{base}-{module.pk}" if counter == 0 else f"{base}-{module.pk}-{counter}"
                target = normalize(suffix)
                counter += 1

        module.path = target
        module.save(update_fields=["path"])
        taken_paths.add(target)


class Migration(migrations.Migration):

    dependencies = [
        ("modules", "0002_initial"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
