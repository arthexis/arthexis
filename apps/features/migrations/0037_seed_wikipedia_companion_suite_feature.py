"""Historical no-op placeholder for the removed Wikipedia Companion feature."""

from django.db import migrations


def noop(apps, schema_editor):
    """Preserve migration graph compatibility without seeding removed feature metadata."""

    del apps, schema_editor


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0036_seed_ocpp_forwarder_suite_feature"),
    ]

    operations = [
        migrations.RunPython(
            noop,
            reverse_code=noop,
        ),
    ]
