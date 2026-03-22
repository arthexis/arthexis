"""Seed the Usage Analytics suite feature for upgraded deployments."""

from django.db import migrations


FEATURE_SLUG = "usage-analytics"
MAIN_APP_LABEL = "core"


def seed_usage_analytics_suite_feature(apps, schema_editor):
    """Create or update the Usage Analytics suite feature.

    Parameters:
        apps: Django migration app registry.
        schema_editor: Active migration schema editor.

    Returns:
        None.
    """

    db_alias = schema_editor.connection.alias
    Application = apps.get_model("app", "Application")
    Feature = apps.get_model("features", "Feature")

    main_app, _ = Application.objects.using(db_alias).update_or_create(
        name=MAIN_APP_LABEL,
        defaults={"enabled": True},
    )
    feature_manager = getattr(Feature, "all_objects", Feature._base_manager).using(db_alias)
    feature_manager.update_or_create(
        slug=FEATURE_SLUG,
        defaults={
            "display": "Usage Analytics",
            "summary": (
                "Collect request and model usage events for historical operator "
                "reporting."
            ),
            "is_enabled": True,
            "main_app": main_app,
            "node_feature": None,
            "admin_requirements": (
                "Allow administrators to review stored usage events and summary "
                "dashboards even when collection is paused."
            ),
            "public_requirements": "",
            "service_requirements": (
                "Collect request and model events only while the suite feature is "
                "enabled, while preserving access to historical summaries and exports."
            ),
            "admin_views": [
                "admin:core_usageevent_changelist",
            ],
            "public_views": [],
            "service_views": [
                "core:usage-analytics-summary",
                "apps.core.management.commands.analytics.Command",
            ],
            "code_locations": [
                "apps/core/analytics.py",
                "apps/core/admin/usage.py",
                "apps/core/views/usage_analytics.py",
                "apps/core/management/commands/analytics.py",
                "config/middleware.py",
            ],
            "protocol_coverage": {},
            "metadata": {
                "disable_policy": "pause_collection_keep_history_viewable",
            },
            "is_seed_data": True,
            "is_deleted": False,
            "source": "mainstream",
        },
    )


def unseed_usage_analytics_suite_feature(apps, schema_editor):
    """Delete the Usage Analytics suite feature for migration rollbacks.

    Parameters:
        apps: Django migration app registry.
        schema_editor: Active migration schema editor.

    Returns:
        None.
    """

    db_alias = schema_editor.connection.alias
    Feature = apps.get_model("features", "Feature")
    feature_manager = getattr(Feature, "all_objects", Feature._base_manager).using(db_alias)
    feature_manager.filter(slug=FEATURE_SLUG).delete()


class Migration(migrations.Migration):
    """Seed the Usage Analytics suite feature for upgraded deployments."""

    dependencies = [
        ("features", "0048_remove_development_blog_feature"),
    ]

    operations = [
        migrations.RunPython(
            seed_usage_analytics_suite_feature,
            reverse_code=unseed_usage_analytics_suite_feature,
        ),
    ]
