from django.db import migrations


RETIRED_NODE_FEATURE_SLUGS = (
    "ap-router",
    "audio-capture",
)


def _cleanup_retired_node_features(apps, schema_editor):
    del schema_editor

    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeFeatureAssignment = apps.get_model("nodes", "NodeFeatureAssignment")

    retired_feature_ids = list(
        NodeFeature.objects.filter(slug__in=RETIRED_NODE_FEATURE_SLUGS).values_list("id", flat=True)
    )
    if not retired_feature_ids:
        return

    NodeFeatureAssignment.objects.filter(feature_id__in=retired_feature_ids).delete()
    NodeFeature.objects.filter(id__in=retired_feature_ids).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0009_remove_node_upgrade_canaries_and_more"),
    ]

    operations = [
        migrations.RunPython(
            _cleanup_retired_node_features,
            migrations.RunPython.noop,
        ),
    ]
