from django.db import migrations


def rename_camera_feature(apps, schema_editor):
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeFeatureAssignment = apps.get_model("nodes", "NodeFeatureAssignment")
    rpi_feature = NodeFeature.objects.filter(slug="rpi-camera").first()
    if not rpi_feature:
        return

    video_feature = NodeFeature.objects.filter(slug="video-cam").first()
    if video_feature:
        NodeFeatureAssignment.objects.filter(feature_id=rpi_feature.id).update(
            feature_id=video_feature.id
        )
        feature_roles = NodeFeature.roles.through
        existing_roles = set(
            feature_roles.objects.filter(nodefeature_id=video_feature.id).values_list(
                "noderole_id", flat=True
            )
        )
        rpi_roles = feature_roles.objects.filter(
            nodefeature_id=rpi_feature.id
        ).values_list("noderole_id", flat=True)
        to_create = [
            feature_roles(
                nodefeature_id=video_feature.id, noderole_id=role_id
            )
            for role_id in rpi_roles
            if role_id not in existing_roles
        ]
        if to_create:
            feature_roles.objects.bulk_create(to_create)
        feature_roles.objects.filter(nodefeature_id=rpi_feature.id).delete()
        rpi_feature.delete()
        return

    rpi_feature.slug = "video-cam"
    rpi_feature.display = "Video Camera"
    rpi_feature.save(update_fields=["slug", "display"])


def restore_camera_feature(apps, schema_editor):
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    if NodeFeature.objects.filter(slug="rpi-camera").exists():
        return
    NodeFeature.objects.filter(slug="video-cam").update(
        slug="rpi-camera", display="Raspberry Pi Camera"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0022_update_platforms"),
    ]

    operations = [
        migrations.RunPython(rename_camera_feature, restore_camera_feature),
    ]
