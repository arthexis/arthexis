"""Retire the historical development blog feature rename."""

from django.db import migrations


def noop_rename_feature(apps, schema_editor):
    """Preserve migration history after removing the retired blog feature."""

    del apps, schema_editor


def noop_restore_feature(apps, schema_editor):
    """Reverse the retired blog feature rename without changing stored data."""

    del apps, schema_editor


class Migration(migrations.Migration):
    """Retire the historical development blog feature rename."""

    dependencies = [
        ("features", "0017_merge_20260224_2131"),
    ]

    operations = [
        migrations.RunPython(noop_rename_feature, noop_restore_feature),
    ]
