"""Retire blog sigil seed roots after the blog app removal."""

from django.db import migrations


def noop_seed_roots(apps, schema_editor):
    """Keep historical migration state stable without seeding removed blog roots."""

    del apps, schema_editor


def noop_unseed_roots(apps, schema_editor):
    """Reverse the retired blog root migration without touching live sigil data."""

    del apps, schema_editor


class Migration(migrations.Migration):
    """Retire historical blog sigil root seeding."""

    dependencies = [
        ("sigils", "0004_protect_sigil_roots"),
    ]

    operations = [
        migrations.RunPython(noop_seed_roots, noop_unseed_roots),
    ]
