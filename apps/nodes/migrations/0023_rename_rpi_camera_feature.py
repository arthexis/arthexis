from django.db import migrations


def rename_camera_feature(apps, schema_editor):
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeFeature.objects.filter(slug="rpi-camera").update(
        slug="video-cam", display="Video Camera"
    )
    NodeFeature.objects.filter(slug="video-cam").update(display="Video Camera")


def restore_camera_feature(apps, schema_editor):
    NodeFeature = apps.get_model("nodes", "NodeFeature")
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
