"""Seed default taskbar icon based on the admin favicon."""

from django.db import migrations


DEFAULT_ICON_B64 = (
    "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"
    "mCC"
)


def seed_default_icon(apps, schema_editor):
    """Create or update the default taskbar icon using admin favicon data."""

    TaskbarIcon = apps.get_model("taskbar", "TaskbarIcon")
    icon, created = TaskbarIcon.objects.get_or_create(
        slug="admin-favicon",
        defaults={
            "name": "Admin Favicon",
            "icon_b64": DEFAULT_ICON_B64,
            "is_default": True,
            "is_seed_data": True,
            "is_deleted": False,
        },
    )
    updated_fields = []
    if icon.name != "Admin Favicon":
        icon.name = "Admin Favicon"
        updated_fields.append("name")
    if icon.icon_b64 != DEFAULT_ICON_B64:
        icon.icon_b64 = DEFAULT_ICON_B64
        updated_fields.append("icon_b64")
    if not icon.is_default:
        icon.is_default = True
        updated_fields.append("is_default")
    if not icon.is_seed_data:
        icon.is_seed_data = True
        updated_fields.append("is_seed_data")
    if icon.is_deleted:
        icon.is_deleted = False
        updated_fields.append("is_deleted")
    if updated_fields:
        icon.save(update_fields=updated_fields)


def unseed_default_icon(apps, schema_editor):
    """Remove seeded admin favicon icon when migration is reversed."""

    TaskbarIcon = apps.get_model("taskbar", "TaskbarIcon")
    TaskbarIcon.objects.filter(slug="admin-favicon").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("taskbar", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_default_icon, reverse_code=unseed_default_icon),
    ]
