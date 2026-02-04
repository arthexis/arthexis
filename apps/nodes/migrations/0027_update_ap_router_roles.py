from django.db import migrations


def add_control_role_to_ap_router(apps, schema_editor):
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeRole = apps.get_model("nodes", "NodeRole")

    try:
        feature = NodeFeature.objects.get(slug="ap-router")
    except NodeFeature.DoesNotExist:
        return

    control_role = NodeRole.objects.filter(name="Control").first()
    if not control_role:
        return

    feature.roles.add(control_role)


def remove_control_role_from_ap_router(apps, schema_editor):
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeRole = apps.get_model("nodes", "NodeRole")

    try:
        feature = NodeFeature.objects.get(slug="ap-router")
    except NodeFeature.DoesNotExist:
        return

    control_role = NodeRole.objects.filter(name="Control").first()
    if not control_role:
        return

    feature.roles.remove(control_role)


class Migration(migrations.Migration):
    dependencies = [
        ("nodes", "0026_update_fast_lane_policy"),
    ]

    operations = [
        migrations.RunPython(
            add_control_role_to_ap_router,
            reverse_code=remove_control_role_from_ap_router,
        ),
    ]
