from django.db import migrations


def link_llm_summary_suite_feature(apps, schema_editor):
    Feature = apps.get_model("features", "Feature")
    NodeFeature = apps.get_model("nodes", "NodeFeature")

    llm_summary_feature, _created = NodeFeature.objects.get_or_create(
        slug="llm-summary",
        defaults={"display": "LLM Summary"},
    )
    Feature.objects.filter(slug="llm-summary-suite").update(
        node_feature=llm_summary_feature,
    )


def restore_lcd_summary_suite_feature(apps, schema_editor):
    Feature = apps.get_model("features", "Feature")
    NodeFeature = apps.get_model("nodes", "NodeFeature")

    lcd_feature, _created = NodeFeature.objects.get_or_create(
        slug="lcd-screen",
        defaults={"display": "LCD Screen"},
    )
    Feature.objects.filter(slug="llm-summary-suite").update(
        node_feature=lcd_feature,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0007_shorten_feature_slugs"),
        ("nodes", "0012_upgrade_policy_custom_controls"),
    ]

    operations = [
        migrations.RunPython(
            link_llm_summary_suite_feature,
            restore_lcd_summary_suite_feature,
        ),
    ]
