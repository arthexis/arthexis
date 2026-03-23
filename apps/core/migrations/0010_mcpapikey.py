"""Deprecated no-op migration retained for historical compatibility."""

from django.db import migrations

from utils.migration_branches import SafelyDeprecatedMigration


class Migration(migrations.Migration):
    """No-op migration kept for compatibility with prior migration history."""

    dependencies = [
        ("core", "0009_alter_invitelead_status"),
    ]

    operations = [
        SafelyDeprecatedMigration(reason="deprecated compatibility shim for removed app split"),
    ]
