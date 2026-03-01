from django.db import migrations


def add_taskbar_display_feature(apps, schema_editor):
    """Seed the taskbar display node feature and assign default roles."""

    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeRole = apps.get_model("nodes", "NodeRole")

    feature, _ = NodeFeature._base_manager.update_or_create(
        slug="taskbar-display",
        defaults={
            "display": "Taskbar Display",
            "is_seed_data": True,
            "is_deleted": False,
        },
    )

    for role_name in ("Terminal", "Control"):
        role = NodeRole.objects.filter(name=role_name).first()
        if role:
            feature.roles.add(role)


def remove_taskbar_display_feature(apps, schema_editor):
    """Remove seeded taskbar display feature during rollback."""

    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeFeature._base_manager.filter(slug="taskbar-display")._raw_delete(
        schema_editor.connection.alias
    )


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
