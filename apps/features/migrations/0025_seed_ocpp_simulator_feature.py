from django.db import migrations


OCPP_SIMULATOR_FEATURE_SLUG = "ocpp-simulator"
LEGACY_NODE_FEATURE_SLUG = "cpsim-service"


def seed_ocpp_simulator_suite_feature(apps, schema_editor):
    """Create or update the OCPP Simulator suite feature and migrate enabled state."""

    Feature = apps.get_model("features", "Feature")
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeFeatureAssignment = apps.get_model("nodes", "NodeFeatureAssignment")
    db_alias = schema_editor.connection.alias

    legacy_feature = (
        NodeFeature.objects.using(db_alias)
        .filter(slug=LEGACY_NODE_FEATURE_SLUG)
        .first()
    )
    was_enabled = False
    if legacy_feature is not None:
        was_enabled = NodeFeatureAssignment.objects.using(db_alias).filter(
            feature=legacy_feature
        ).exists()

    feature, _ = Feature.objects.using(db_alias).update_or_create(
        slug=OCPP_SIMULATOR_FEATURE_SLUG,
        defaults={
            "display": "OCPP Simulator",
            "summary": "Controls access to OCPP simulator workflows and service toggles.",
            "is_enabled": was_enabled,
            "source": "mainstream",
            "node_feature": None,
            "admin_requirements": "Admin can start and stop simulator service from OCPP simulator pages.",
            "public_requirements": "",
            "service_requirements": "Background CPSim process can be toggled by admin lock request.",
            "admin_views": ["admin:ocpp_simulator_changelist"],
            "public_views": [],
            "service_views": [],
            "code_locations": [
                "apps/ocpp/cpsim_service.py",
                "apps/ocpp/admin/miscellaneous/simulator_admin.py",
            ],
            "protocol_coverage": {},
        },
    )
    # Ensure suite feature is not bound to legacy node feature dependency.
    if feature.node_feature_id is not None:
        feature.node_feature = None
        feature.save(update_fields=["node_feature"])


def unseed_ocpp_simulator_suite_feature(apps, schema_editor):
    """Restore legacy node assignment state from the suite feature and remove it."""

    Feature = apps.get_model("features", "Feature")
    Node = apps.get_model("nodes", "Node")
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeFeatureAssignment = apps.get_model("nodes", "NodeFeatureAssignment")
    db_alias = schema_editor.connection.alias

    suite_feature = (
        Feature.objects.using(db_alias)
        .filter(slug=OCPP_SIMULATOR_FEATURE_SLUG)
        .first()
    )

    legacy_feature, _ = NodeFeature.objects.using(db_alias).get_or_create(
        slug=LEGACY_NODE_FEATURE_SLUG,
        defaults={
            "display": "OCPP Simulator",
            "is_seed_data": True,
            "is_deleted": False,
        },
    )
    if suite_feature is not None and suite_feature.is_enabled:
        local_node = Node.objects.using(db_alias).order_by("pk").first()
        if local_node is not None:
            NodeFeatureAssignment.objects.using(db_alias).get_or_create(
                node=local_node,
                feature=legacy_feature,
            )

    Feature.objects.using(db_alias).filter(slug=OCPP_SIMULATOR_FEATURE_SLUG).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0024_merge_20260228_1838"),
        ("nodes", "0036_merge_20260228_2001"),
    ]

    operations = [
        migrations.RunPython(
            seed_ocpp_simulator_suite_feature,
            unseed_ocpp_simulator_suite_feature,
        ),
    ]
