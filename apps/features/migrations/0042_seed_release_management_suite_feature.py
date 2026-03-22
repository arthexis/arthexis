"""Seed the Release Management suite feature."""

from django.db import migrations


RELEASE_MANAGEMENT_FEATURE_SLUG = "release-management"


def seed_release_management_suite_feature(apps, schema_editor):
    """Create or update the Release Management feature with execution-mode toggle."""

    del schema_editor
    Application = apps.get_model("app", "Application")
    Feature = apps.get_model("features", "Feature")

    release_app = Application.objects.filter(name="release").first()
    defaults = {
        "display": "Release Management",
        "source": "mainstream",
        "summary": (
            "Centralize repository, package, and GitHub release operations with token-aware "
            "suite logic and CLI fallback."
        ),
        "is_enabled": True,
        "main_app": release_app,
        "node_feature": None,
        "admin_requirements": (
            "Allow operators to choose whether Release Management prefers suite tokens/API or "
            "GitHub CLI binaries and local auth."
        ),
        "public_requirements": "",
        "service_requirements": (
            "Default suite mode must use suite-managed GitHub token flows when available and "
            "fallback to gh/git when suite tokens are unavailable."
        ),
        "admin_views": ["admin:features_feature_change"],
        "public_views": [],
        "service_views": [
            "apps.repos.release_management.ReleaseManagementClient",
            "apps.repos.management.commands.repo.Command",
        ],
        "code_locations": [
            "apps/repos/release_management.py",
            "apps/repos/management/commands/repo.py",
        ],
        "protocol_coverage": {},
        "metadata": {
            "parameters": {
                "execution_mode": "suite",
            }
        },
    }

    Feature.objects.update_or_create(
        slug=RELEASE_MANAGEMENT_FEATURE_SLUG,
        defaults=defaults,
    )


def unseed_release_management_suite_feature(apps, schema_editor):
    """Delete the seeded Release Management suite feature."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")
    Feature.objects.filter(slug=RELEASE_MANAGEMENT_FEATURE_SLUG).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0041_merge_20260309_0703"),
    ]

    operations = [
        migrations.RunPython(
            seed_release_management_suite_feature,
            unseed_release_management_suite_feature,
        ),
    ]
