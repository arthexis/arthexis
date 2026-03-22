"""Seed the GitHub Issue Reporting suite feature."""

from django.conf import settings
from django.db import migrations


FEATURE_SLUG = "github-issue-reporting"


def seed_github_issue_reporting_suite_feature(apps, schema_editor):
    """Create or update the GitHub Issue Reporting suite feature."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")

    Feature.objects.update_or_create(
        slug=FEATURE_SLUG,
        defaults={
            "display": "GitHub Issue Reporting",
            "source": "mainstream",
            "summary": (
                "Automatically enqueue GitHub issue reporting for uncaught request "
                "exceptions while preserving duplicate-exception cooldown behavior."
            ),
            "is_enabled": getattr(settings, "GITHUB_ISSUE_REPORTING_ENABLED", True),
            "admin_requirements": (
                "Expose the runtime toggle from feedback and repository configuration "
                "surfaces so administrators can pause or resume automatic reporting."
            ),
            "service_requirements": (
                "Connect the request-exception signal, respect suite feature state at "
                "runtime, and queue GitHub reporting tasks only when enabled."
            ),
            "admin_views": [
                "admin:repos_repositoryissue_changelist",
                "admin:repos_repositoryissue_configure",
            ],
            "service_views": [
                "django.core.signals.got_request_exception",
                "apps.repos.apps.queue_github_issue",
                "apps.tasks.tasks.report_exception_to_github",
            ],
            "code_locations": [
                "apps/repos/apps.py",
                "apps/repos/admin_feedback_config.py",
                "apps/repos/issue_reporting.py",
                "apps/tasks/tasks.py",
            ],
        },
    )


def unseed_github_issue_reporting_suite_feature(apps, schema_editor):
    """Delete the seeded GitHub Issue Reporting suite feature."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")
    Feature.objects.filter(slug=FEATURE_SLUG).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0048_remove_development_blog_feature"),
    ]

    operations = [
        migrations.RunPython(
            seed_github_issue_reporting_suite_feature,
            reverse_code=unseed_github_issue_reporting_suite_feature,
        ),
    ]
