"""Remove obsolete blog sigil roots from existing installations."""

from django.db import migrations


BLOG_SIGIL_ROOT_PREFIXES = ("blog", "code")
BLOG_SIGIL_ROOT_DEFAULTS = {
    "blog": {
        "context_type": "request",
        "content_type": None,
        "is_seed_data": True,
        "is_deleted": False,
    },
    "code": {
        "context_type": "request",
        "content_type": None,
        "is_seed_data": True,
        "is_deleted": False,
    },
}


def remove_blog_roots(apps, schema_editor):
    """Hard-delete retired blog sigil roots that were seeded before app removal.

    Parameters:
        apps: Django migration app registry.
        schema_editor: Active migration schema editor.

    Returns:
        None.
    """

    SigilRoot = apps.get_model("sigils", "SigilRoot")
    db_alias = schema_editor.connection.alias
    manager = getattr(SigilRoot, "all_objects", SigilRoot._base_manager).using(db_alias)
    manager.filter(prefix__in=BLOG_SIGIL_ROOT_PREFIXES)._raw_delete(db_alias)


def restore_blog_roots(apps, schema_editor):
    """Recreate retired blog sigil roots when rolling the cleanup migration back.

    Parameters:
        apps: Django migration app registry.
        schema_editor: Active migration schema editor.

    Returns:
        None.
    """

    SigilRoot = apps.get_model("sigils", "SigilRoot")
    db_alias = schema_editor.connection.alias
    manager = getattr(SigilRoot, "all_objects", SigilRoot._base_manager).using(db_alias)
    for prefix in BLOG_SIGIL_ROOT_PREFIXES:
        manager.update_or_create(prefix=prefix, defaults=BLOG_SIGIL_ROOT_DEFAULTS[prefix])


class Migration(migrations.Migration):
    """Remove obsolete blog sigil roots from existing installations."""

    dependencies = [
        ("sigils", "0005_seed_blog_sigil_roots"),
    ]

    operations = [
        migrations.RunPython(remove_blog_roots, restore_blog_roots),
    ]
