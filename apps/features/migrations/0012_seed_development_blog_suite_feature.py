"""Seed the Development Blog suite feature and regression guard metadata."""

from django.db import migrations


FEATURE_SLUG = "development-blog-suite"

FEATURE_SUMMARY = """Engineering blog suite for publishing deep technical guides quickly.

Included capabilities:
1. Structured long-form article editor with markdown/html body modes.
2. Multi-stage editorial workflow (draft, in-review, scheduled, published, archived).
3. Time-based scheduling and automated publication for queued content.
4. Series support for multi-part guides and implementation playbooks.
5. Tag taxonomy for language, stack, and subsystem indexing.
6. Reviewer assignment for engineering peer review.
7. Immutable revision snapshots for change auditing.
8. Code references with repository path + line range anchors.
9. Inline code-citation sigils for fast embedding and highlighting.
10. Specialized per-article sigil shortcuts for repetitive snippets.
11. Reading-time estimation for developer-focused UX.
12. Featured-article controls for release communication.
13. Canonical URL support for mirrored docs/blog publishing.
14. Public index/detail endpoints for discoverable engineering content.
15. Admin-first operations with inlines for code refs and sigil shortcuts.
16. Scheduled publishing management command for automation and cron jobs.
"""


def seed_feature(apps, schema_editor):
    del schema_editor
    Feature = apps.get_model("features", "Feature")
    Feature.objects.update_or_create(
        slug=FEATURE_SLUG,
        defaults={
            "display": "Development Blog suite feature",
            "summary": FEATURE_SUMMARY,
            "is_enabled": True,
            "admin_requirements": "Admin CRUD for articles, code references, shortcuts, and revisions.",
            "public_requirements": "Public engineering blog list/detail pages.",
            "service_requirements": "Background publishing via management command and scheduler.",
            "admin_views": [
                "admin:blog_blogarticle_changelist",
                "admin:blog_blogseries_changelist",
            ],
            "public_views": [
                "blog-list",
                "blog-detail",
            ],
            "service_views": [
                "manage.py publish_scheduled_articles",
            ],
            "code_locations": [
                "apps/blog/models.py",
                "apps/blog/admin.py",
                "apps/blog/views.py",
                "apps/blog/sigils.py",
                "apps/blog/management/commands/publish_scheduled_articles.py",
            ],
            "protocol_coverage": {},
        },
    )


def unseed_feature(apps, schema_editor):
    del schema_editor
    Feature = apps.get_model("features", "Feature")
    Feature.objects.filter(slug=FEATURE_SLUG).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0011_rework_evergo_api_client_feature"),
    ]

    operations = [
        migrations.RunPython(seed_feature, unseed_feature),
    ]
