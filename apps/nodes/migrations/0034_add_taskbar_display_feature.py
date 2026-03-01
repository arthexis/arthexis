from django.db import migrations


def add_taskbar_display_feature(apps, schema_editor):
    """Seed the taskbar display node feature and assign default roles."""

    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeRole = apps.get_model("nodes", "NodeRole")

    feature, _ = NodeFeature.objects.get_or_create(
        slug="taskbar-display",
        defaults={
            "display": "Taskbar Display",
            "is_seed_data": True,
            "is_deleted": False,
        },
    )

    updated_fields = []
    if feature.display != "Taskbar Display":
        feature.display = "Taskbar Display"
        updated_fields.append("display")
    if not feature.is_seed_data:
        feature.is_seed_data = True
        updated_fields.append("is_seed_data")
    if feature.is_deleted:
        feature.is_deleted = False
        updated_fields.append("is_deleted")
    if updated_fields:
        feature.save(update_fields=updated_fields)

    for role_name in ("Terminal", "Control"):
        role = NodeRole.objects.filter(name=role_name).first()
        if role:
            feature.roles.add(role)


def remove_taskbar_display_feature(apps, schema_editor):
    """Remove seeded taskbar display feature during rollback."""

    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeFeature.objects.filter(slug="taskbar-display").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("nodes", "0033_merge_20260228_1251"),
    ]

    operations = [
        migrations.RunPython(
            add_taskbar_display_feature,
            reverse_code=remove_taskbar_display_feature,
        )
    ]
