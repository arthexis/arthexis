from django.db import migrations


def normalize(path):
    """Return the normalized module path used by the historical migration."""

    if path is None:
        return None
    stripped = str(path).strip("/")
    return "/" if stripped == "" else f"/{stripped}/"


def forwards(apps, schema_editor):
    """Defer path normalization to the checkpointed release transform pipeline."""

    del apps, schema_editor


class Migration(migrations.Migration):

    dependencies = [
        ("modules", "0002_initial"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
