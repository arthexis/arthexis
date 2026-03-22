from django.db import migrations


def remove_seed_nodes(apps, schema_editor):
    """Defer seed-node cleanup to the existing deferred node migration task."""

    del apps, schema_editor


class Migration(migrations.Migration):
    dependencies = [
        ("nodes", "0004_remove_invalid_clipboard_tasks"),
    ]

    operations = [
        migrations.RunPython(remove_seed_nodes, reverse_code=migrations.RunPython.noop),
    ]
