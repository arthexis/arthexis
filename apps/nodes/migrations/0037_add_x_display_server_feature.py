from django.db import migrations


def add_x_display_server_feature(apps, schema_editor):
    """Add the x-display-server node feature seed entry."""

    NodeFeature = apps.get_model("nodes", "NodeFeature")
    feature, created = NodeFeature.objects.get_or_create(
        slug="x-display-server",
        defaults={
            "display": "X Display Server",
            "is_seed_data": True,
            "is_deleted": False,
        },
    )
    if not created and feature.display != "X Display Server":
        feature.display = "X Display Server"
        feature.save(update_fields=["display"])


def remove_x_display_server_feature(apps, schema_editor):
    """Remove the x-display-server node feature seed entry."""

    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeFeature.objects.filter(slug="x-display-server").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("nodes", "0036_merge_20260228_2001"),
    ]

    operations = [
        migrations.RunPython(
            add_x_display_server_feature,
            remove_x_display_server_feature,
        ),
    ]
