"""Compatibility migration after moving MCP models into apps.mcp."""

from django.db import migrations


class Migration(migrations.Migration):
    """No-op migration kept for compatibility with prior migration history."""

    dependencies = [
        ("core", "0009_alter_invitelead_status"),
        ("mcp", "0001_initial"),
    ]

    operations = []
