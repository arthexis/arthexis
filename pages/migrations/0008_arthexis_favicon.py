from __future__ import annotations

import base64
from pathlib import Path

from django.core.files.base import ContentFile
from django.db import migrations


FAVICON_FILENAME = "arthexis.png"
FAVICON_SOURCE = Path(__file__).resolve().parents[1] / "fixtures" / "data" / "favicon_arthexis.txt"


def load_favicon_content() -> bytes:
    return base64.b64decode(FAVICON_SOURCE.read_text().strip())


def apply_arthexis_favicon(apps, schema_editor):
    Site = apps.get_model("sites", "Site")
    SiteBadge = apps.get_model("pages", "SiteBadge")

    site = Site.objects.filter(domain="arthexis.com").first()
    if not site:
        return

    badge, created = SiteBadge.all_objects.get_or_create(site=site)

    if not created and (badge.is_user_data or badge.favicon):
        return

    badge.badge_color = badge.badge_color or "#28a745"
    badge.is_seed_data = True
    badge.is_user_data = False
    badge.is_deleted = False

    if badge.favicon:
        badge.favicon.delete(save=False)

    content = ContentFile(load_favicon_content())
    badge.favicon.save(FAVICON_FILENAME, content, save=False)
    badge.save()


def remove_arthexis_favicon(apps, schema_editor):
    Site = apps.get_model("sites", "Site")
    SiteBadge = apps.get_model("pages", "SiteBadge")

    site = Site.objects.filter(domain="arthexis.com").first()
    if not site:
        return

    badge = SiteBadge.all_objects.filter(site=site).first()
    if not badge:
        return

    if badge.favicon:
        badge.favicon.delete(save=False)
        badge.favicon = ""
        badge.save(update_fields=["favicon"])


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0007_usermanual"),
    ]

    operations = [
        migrations.RunPython(apply_arthexis_favicon, remove_arthexis_favicon),
    ]
