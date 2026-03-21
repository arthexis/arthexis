"""Add sigil roots for development blog entities and code citation helpers."""

from django.db import migrations


def seed_blog_roots(apps, schema_editor):
    del schema_editor
    SigilRoot = apps.get_model("sigils", "SigilRoot")

    SigilRoot.objects.update_or_create(
        prefix="blog",
        defaults={
            "context_type": "request",
            "content_type": None,
            "is_seed_data": True,
            "is_deleted": False,
        },
    )
    SigilRoot.objects.update_or_create(
        prefix="code",
        defaults={
            "context_type": "request",
            "content_type": None,
            "is_seed_data": True,
            "is_deleted": False,
        },
    )


def unseed_blog_roots(apps, schema_editor):
    del schema_editor
    SigilRoot = apps.get_model("sigils", "SigilRoot")
    SigilRoot.objects.filter(prefix__in=["blog", "code"]).update(is_seed_data=False)


class Migration(migrations.Migration):

    dependencies = [
        ("sigils", "0004_protect_sigil_roots"),
    ]

    operations = [
        migrations.RunPython(seed_blog_roots, unseed_blog_roots),
    ]
