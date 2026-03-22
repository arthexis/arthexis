"""Remove obsolete blog application registry rows after retiring the app."""

from django.db import migrations

RETIRED_BLOG_APPLICATION_NAMES = ("apps.blog", "blog")


def remove_retired_blog_applications(apps, schema_editor):
    """Delete stale application rows that still point at the retired blog app.

    Args:
        apps: Historical app registry provided by Django migrations.
        schema_editor: Active schema editor for the migration run.

    Returns:
        None.
    """

    del schema_editor
    Application = apps.get_model("app", "Application")
    Application.objects.filter(name__in=RETIRED_BLOG_APPLICATION_NAMES).delete()


class Migration(migrations.Migration):
    """Remove obsolete blog application rows after retiring the app."""

    dependencies = [
        ("app", "0010_archive_wikis_bridge_table"),
    ]

    operations = [
        migrations.RunPython(
            remove_retired_blog_applications,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
