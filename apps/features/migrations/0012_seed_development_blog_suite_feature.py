"""Retire the historical development blog feature seed."""

from django.db import migrations


def noop_seed_feature(apps, schema_editor):
    """Preserve migration ordering without seeding the retired blog feature."""

    del apps, schema_editor


def noop_unseed_feature(apps, schema_editor):
    """Reverse the retired blog feature seed without touching stored data."""

    del apps, schema_editor


class Migration(migrations.Migration):
    """Retire the historical development blog feature seed."""

    dependencies = [
        ("features", "0011_rework_evergo_api_client_feature"),
    ]

    operations = [
        migrations.RunPython(noop_seed_feature, noop_unseed_feature),
    ]
