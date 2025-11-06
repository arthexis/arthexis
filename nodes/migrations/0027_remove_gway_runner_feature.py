from django.db import migrations


def remove_gway_runner_feature(apps, schema_editor):
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeFeatureAssignment = apps.get_model(
        "nodes", "NodeFeatureAssignment"
    )

    feature_manager = getattr(NodeFeature, "all_objects", NodeFeature.objects)
    assignment_manager = getattr(
        NodeFeatureAssignment, "all_objects", NodeFeatureAssignment.objects
    )

    try:
        feature = feature_manager.get(slug="gway-runner")
    except NodeFeature.DoesNotExist:
        return

    assignment_manager.filter(feature_id=feature.pk).delete()
    feature.roles.clear()
    feature.delete()


def restore_gway_runner_feature(apps, schema_editor):
    NodeFeature = apps.get_model("nodes", "NodeFeature")

    feature_manager = getattr(NodeFeature, "all_objects", NodeFeature.objects)

    feature, created = feature_manager.get_or_create(
        slug="gway-runner",
        defaults={
            "display": "gway Runner",
            "is_seed_data": True,
        },
    )

    updated_fields = []

    if feature.display != "gway Runner":
        feature.display = "gway Runner"
        updated_fields.append("display")

    if feature.is_deleted:
        feature.is_deleted = False
        updated_fields.append("is_deleted")

    if not feature.is_seed_data:
        feature.is_seed_data = True
        updated_fields.append("is_seed_data")

    if updated_fields:
        feature.save(update_fields=updated_fields)

    feature.roles.clear()


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0026_alter_node_port"),
    ]

    operations = [
        migrations.RunPython(
            remove_gway_runner_feature, restore_gway_runner_feature
        )
    ]
