from django.db import migrations, models


HEAVY_FEATURE_SLUGS = {
    "ap-router",
    "celery-queue",
    "nginx-server",
}


def mark_heavy_node_features(apps, schema_editor):
    """Mark environment-changing node features as heavy footprint."""

    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeFeature.objects.filter(slug__in=HEAVY_FEATURE_SLUGS).update(footprint="heavy")


def mark_light_node_features(apps, schema_editor):
    """Restore heavy node features to light footprint for rollback."""

    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeFeature.objects.filter(slug__in=HEAVY_FEATURE_SLUGS).update(footprint="light")


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0034_merge_20260228_1838"),
    ]

    operations = [
        migrations.AddField(
            model_name="nodefeature",
            name="footprint",
            field=models.CharField(
                choices=[("light", "Light"), ("heavy", "Heavy")],
                default="light",
                help_text=(
                    "Classifies whether the feature is lightweight or may modify host "
                    "environment configuration."
                ),
                max_length=10,
            ),
        ),
        migrations.RunPython(mark_heavy_node_features, mark_light_node_features),
    ]
