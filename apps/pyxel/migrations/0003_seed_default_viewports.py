from __future__ import annotations

import json
from pathlib import Path

from django.db import migrations

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "pyxel_viewports__defaults.json"


def seed_default_viewports(apps, schema_editor):
    """Seed the default Pyxel viewport presets from fixture data."""

    PyxelViewport = apps.get_model("pyxel", "PyxelViewport")
    with FIXTURE_PATH.open("r", encoding="utf-8") as fixture_file:
        fixture_rows = json.load(fixture_file)

    for row in fixture_rows:
        fields = row.get("fields", {})
        slug = fields["slug"]
        defaults = dict(fields)
        defaults.pop("slug", None)
        PyxelViewport.objects.update_or_create(slug=slug, defaults=defaults)


def unseed_default_viewports(apps, schema_editor):
    """Remove viewport presets created by ``seed_default_viewports``."""

    PyxelViewport = apps.get_model("pyxel", "PyxelViewport")
    with FIXTURE_PATH.open("r", encoding="utf-8") as fixture_file:
        fixture_rows = json.load(fixture_file)

    slugs = [row.get("fields", {}).get("slug") for row in fixture_rows]
    slugs = [slug for slug in slugs if slug]
    PyxelViewport.objects.filter(slug__in=slugs, is_seed_data=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("pyxel", "0002_pyxelviewport_is_default_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_default_viewports, unseed_default_viewports),
    ]
