"""Historical no-op placeholder for the removed Wikipedia Companion feature."""

from django.db import migrations


FEATURE_SLUG = "wikipedia-companion"
FEATURE_SOURCE = "mainstream"


def noop_seed_wikipedia_companion_suite_feature(apps, schema_editor):
    """Preserve migration graph compatibility without seeding removed feature metadata."""

    del apps, schema_editor


def remove_seeded_wikipedia_companion_suite_feature(apps, schema_editor):
    """Restore pre-0037 state by removing the retired mainstream feature row on rollback."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")
    Feature.objects.filter(slug=FEATURE_SLUG, source=FEATURE_SOURCE).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("features", "0036_seed_ocpp_forwarder_suite_feature"),
    ]

    operations = [
        migrations.RunPython(
            noop_seed_wikipedia_companion_suite_feature,
            reverse_code=remove_seeded_wikipedia_companion_suite_feature,
        ),
    ]
