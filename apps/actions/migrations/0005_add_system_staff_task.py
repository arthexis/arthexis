"""Add the System staff task default button entry."""

from django.db import migrations


SYSTEM_TASK = {
    "slug": "system",
    "label": "System",
    "description": "Inspect system details and service controls.",
    "admin_url_name": "admin:system-details",
    "order": 105,
    "default_enabled": True,
    "staff_only": True,
    "superuser_only": False,
    "is_active": True,
}


def add_system_staff_task(apps, schema_editor):
    """Create or update the default System staff task."""

    StaffTask = apps.get_model("actions", "StaffTask")
    StaffTask.objects.update_or_create(
        slug=SYSTEM_TASK["slug"],
        defaults={
            "label": SYSTEM_TASK["label"],
            "description": SYSTEM_TASK["description"],
            "admin_url_name": SYSTEM_TASK["admin_url_name"],
            "order": SYSTEM_TASK["order"],
            "default_enabled": SYSTEM_TASK["default_enabled"],
            "staff_only": SYSTEM_TASK["staff_only"],
            "superuser_only": SYSTEM_TASK["superuser_only"],
            "is_active": SYSTEM_TASK["is_active"],
        },
    )


def remove_system_staff_task(apps, schema_editor):
    """Remove the seeded System staff task."""

    StaffTask = apps.get_model("actions", "StaffTask")
    StaffTask.objects.filter(slug=SYSTEM_TASK["slug"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("actions", "0004_seed_staff_tasks"),
    ]

    operations = [
        migrations.RunPython(add_system_staff_task, remove_system_staff_task),
    ]
