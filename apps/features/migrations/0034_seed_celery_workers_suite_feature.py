"""Seed the Celery Workers suite feature."""

from django.db import migrations


CELERY_WORKERS_FEATURE_SLUG = "celery-workers"


def seed_celery_workers_suite_feature(apps, schema_editor):
    """Create or update the Celery Workers suite feature and parameter defaults."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")
    Feature.objects.update_or_create(
        slug=CELERY_WORKERS_FEATURE_SLUG,
        defaults={
            "display": "Celery Workers",
            "source": "mainstream",
            "summary": "Controls the local Celery worker process count.",
            "is_enabled": True,
            "node_feature": None,
            "admin_requirements": (
                "Expose worker-count controls so admins can tune local task throughput."
            ),
            "public_requirements": "",
            "service_requirements": (
                "Persist worker count to lock files and restart celery worker service after changes."
            ),
            "admin_views": [
                "admin:features_feature_change",
                "admin:services_lifecycleservice_changelist",
            ],
            "public_views": [],
            "service_views": ["apps.services.celery_workers.sync_celery_workers_from_feature"],
            "code_locations": [
                "apps/features/parameters.py",
                "apps/features/admin.py",
                "apps/services/celery_workers.py",
                "apps/services/admin.py",
            ],
            "protocol_coverage": {},
            "metadata": {"parameters": {"worker_count": "1"}},
        },
    )


def unseed_celery_workers_suite_feature(apps, schema_editor):
    """Remove the seeded Celery Workers suite feature."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")
    Feature.objects.filter(slug=CELERY_WORKERS_FEATURE_SLUG).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0033_seed_odoo_crm_sync_features"),
    ]

    operations = [
        migrations.RunPython(
            seed_celery_workers_suite_feature,
            unseed_celery_workers_suite_feature,
        ),
    ]
