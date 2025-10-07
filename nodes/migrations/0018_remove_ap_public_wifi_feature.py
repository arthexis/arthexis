from django.db import migrations


def integrate_ap_public_wifi(apps, schema_editor):
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeFeatureAssignment = apps.get_model("nodes", "NodeFeatureAssignment")

    feature_manager = getattr(NodeFeature, "all_objects", NodeFeature.objects)
    assignment_manager = getattr(
        NodeFeatureAssignment, "all_objects", NodeFeatureAssignment.objects
    )

    try:
        public = feature_manager.get(slug="ap-public-wifi")
    except NodeFeature.DoesNotExist:
        return

    router = feature_manager.filter(slug="ap-router").first()

    assignments = list(assignment_manager.filter(feature_id=public.pk))

    if router:
        for assignment in assignments:
            assignment_manager.get_or_create(
                node_id=assignment.node_id,
                feature_id=router.pk,
                defaults={
                    "is_seed_data": assignment.is_seed_data,
                    "is_user_data": assignment.is_user_data,
                    "is_deleted": assignment.is_deleted,
                },
            )

    for assignment in assignments:
        assignment.delete()

    if not public.is_deleted:
        public.is_deleted = True
        public.save(update_fields=["is_deleted"])


def restore_ap_public_wifi(apps, schema_editor):
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeFeatureAssignment = apps.get_model("nodes", "NodeFeatureAssignment")
    NodeRole = apps.get_model("nodes", "NodeRole")

    feature_manager = getattr(NodeFeature, "all_objects", NodeFeature.objects)
    assignment_manager = getattr(
        NodeFeatureAssignment, "all_objects", NodeFeatureAssignment.objects
    )

    public, _ = feature_manager.get_or_create(
        slug="ap-public-wifi",
        defaults={
            "display": "AP Public Wi-Fi",
            "is_seed_data": True,
        },
    )

    if public.display != "AP Public Wi-Fi":
        public.display = "AP Public Wi-Fi"
    if public.is_deleted:
        public.is_deleted = False
    public.save(update_fields=["display", "is_deleted"])

    control_role = NodeRole.objects.filter(name="Control").first()
    if control_role:
        public.roles.add(control_role)

    router = feature_manager.filter(slug="ap-router").first()
    if not router:
        return

    router_assignments = assignment_manager.filter(feature_id=router.pk)
    for assignment in router_assignments:
        assignment_manager.get_or_create(
            node_id=assignment.node_id,
            feature_id=public.pk,
            defaults={
                "is_seed_data": assignment.is_seed_data,
                "is_user_data": assignment.is_user_data,
                "is_deleted": assignment.is_deleted,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0017_netmessage_attachments"),
    ]

    operations = [
        migrations.RunPython(
            integrate_ap_public_wifi, restore_ap_public_wifi
        )
    ]
