"""Remove retired blog-related sigil roots."""

from django.db import migrations


BLOG_SIGIL_PREFIXES = ["blog", "code"]


def remove_blog_sigil_roots(apps, schema_editor):
    """Delete legacy blog sigil roots that are no longer used."""

    SigilRoot = apps.get_model("sigils", "SigilRoot")
    db_alias = schema_editor.connection.alias
    sigil_manager = getattr(SigilRoot, "all_objects", SigilRoot._base_manager).using(db_alias)
    sigil_manager.filter(
        prefix__in=BLOG_SIGIL_PREFIXES,
        context_type="request",
        content_type=None,
        is_seed_data=True,
    )._raw_delete(db_alias)


def restore_blog_sigil_roots(apps, schema_editor):
    """Recreate blog sigil roots on rollback."""

    db_alias = schema_editor.connection.alias
    SigilRoot = apps.get_model("sigils", "SigilRoot")
    sigil_manager = getattr(SigilRoot, "all_objects", SigilRoot._base_manager).using(db_alias)
    for prefix in BLOG_SIGIL_PREFIXES:
        sigil_manager.update_or_create(
            prefix=prefix,
            defaults={
                "context_type": "request",
                "content_type": None,
                "is_seed_data": True,
                "is_deleted": False,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("sigils", "0005_seed_blog_sigil_roots"),
    ]

    operations = [
        migrations.RunPython(remove_blog_sigil_roots, restore_blog_sigil_roots),
    ]
