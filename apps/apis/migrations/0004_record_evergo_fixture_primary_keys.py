"""Record Evergo endpoint fixture primary key stabilization for idempotent loaddata."""

from django.db import migrations


def noop_forward(apps, schema_editor):
    """No-op data migration to version fixture updates with reversible history."""

    del apps, schema_editor


def noop_reverse(apps, schema_editor):
    """Reverse no-op for fixture version tracking migration."""

    del apps, schema_editor


class Migration(migrations.Migration):
    dependencies = [
        ("apis", "0003_expand_evergo_api_explorer_endpoints"),
    ]

    operations = [
        migrations.RunPython(noop_forward, noop_reverse),
    ]
