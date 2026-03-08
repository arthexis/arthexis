from __future__ import annotations

from django.db import migrations



def set_camera_service_feature_activation(apps, schema_editor):
    """Switch camera-service lifecycle activation to the auto-detected video feature."""

    LifecycleService = apps.get_model("services", "LifecycleService")
    LifecycleService.objects.filter(slug="camera-service").update(
        activation="feature",
        feature_slug="video-cam",
        lock_names=["camera-service.lck"],
    )



def reset_camera_service_feature_activation(apps, schema_editor):
    """Restore lockfile-only activation for camera-service lifecycle rows."""

    LifecycleService = apps.get_model("services", "LifecycleService")
    LifecycleService.objects.filter(slug="camera-service").update(
        activation="lockfile",
        feature_slug="",
        lock_names=["camera-service.lck"],
    )


class Migration(migrations.Migration):

    dependencies = [
        ("services", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            set_camera_service_feature_activation,
            reset_camera_service_feature_activation,
        ),
    ]
