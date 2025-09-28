from __future__ import annotations

from django.db import migrations


NEW_COLOR = "#ff8800"
OLD_COLOR = "#daa520"


def _manager(model, name):
    manager = getattr(model, name, None)
    if manager is not None:
        return manager
    return model.objects


def apply_badge_color(apps, schema_editor):
    Site = apps.get_model("sites", "Site")
    SiteBadge = apps.get_model("pages", "SiteBadge")

    site = Site.objects.filter(domain="arthexis.com").first()
    if not site:
        return

    badge_manager = _manager(SiteBadge, "all_objects")
    badge, _ = badge_manager.get_or_create(site=site)

    if badge.is_user_data:
        return

    updates = {
        "badge_color": NEW_COLOR,
        "is_seed_data": True,
        "is_deleted": False,
        "is_user_data": False,
    }

    for field, value in updates.items():
        setattr(badge, field, value)
    badge.save(update_fields=list(updates.keys()))


def revert_badge_color(apps, schema_editor):
    Site = apps.get_model("sites", "Site")
    SiteBadge = apps.get_model("pages", "SiteBadge")

    site = Site.objects.filter(domain="arthexis.com").first()
    if not site:
        return

    badge_manager = _manager(SiteBadge, "all_objects")
    badge = badge_manager.filter(site=site).first()
    if not badge or badge.is_user_data:
        return

    updates = {
        "badge_color": OLD_COLOR,
        "is_seed_data": True,
        "is_deleted": False,
        "is_user_data": False,
    }
    for field, value in updates.items():
        setattr(badge, field, value)
    badge.save(update_fields=list(updates.keys()))


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0013_ocpp_user_manual"),
    ]

    operations = [
        migrations.RunPython(apply_badge_color, revert_badge_color),
    ]
