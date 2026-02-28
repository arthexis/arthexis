"""Seed the Feedback Ingestion suite feature."""

from django.db import migrations

FEATURE_SLUG = "feedback-ingestion"


def seed_feedback_ingestion_feature(apps, schema_editor):
    """Create or update the Feedback Ingestion suite feature."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")
    Feature.objects.update_or_create(
        slug=FEATURE_SLUG,
        defaults={
            "display": "Feedback Ingestion",
            "source": "mainstream",
            "summary": (
                "Collects user feedback from both the public site and Django admin, "
                "including ratings, comments, optional user messages, and managed "
                "file attachments."
            ),
            "is_enabled": True,
            "admin_requirements": (
                "Render the admin feedback icon and overlay outside login pages, "
                "submit feedback to pages:user-story-submit, and persist entries for "
                "admin triage workflows."
            ),
            "public_requirements": (
                "Render the public feedback icon and overlay, capture page path and "
                "experience rating, accept authenticated or anonymous submissions, and "
                "respect role-based attachment/comment limits."
            ),
            "service_requirements": (
                "Accept POST submissions at pages:user-story-submit, enforce throttling "
                "and validation, store UserStory/UserStoryAttachment records, and reject "
                "submissions when the suite feature is disabled."
            ),
            "admin_views": [
                "admin:index",
                "admin:sites_userstory_changelist",
            ],
            "public_views": [
                "pages:index",
            ],
            "service_views": [
                "POST pages:user-story-submit",
            ],
            "code_locations": [
                "apps/sites/context_processors.py",
                "apps/sites/forms.py",
                "apps/sites/models/user_story.py",
                "apps/sites/templates/admin/base_site.html",
                "apps/sites/templates/admin/includes/user_story_feedback.html",
                "apps/sites/templates/pages/base.html",
                "apps/sites/views/landing.py",
            ],
            "protocol_coverage": {},
        },
    )


def unseed_feedback_ingestion_feature(apps, schema_editor):
    """Remove the seeded Feedback Ingestion suite feature."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")
    Feature.objects.filter(slug=FEATURE_SLUG).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0020_remove_ftp_reports_node_feature_dependency"),
    ]

    operations = [
        migrations.RunPython(
            seed_feedback_ingestion_feature,
            unseed_feedback_ingestion_feature,
        ),
    ]
