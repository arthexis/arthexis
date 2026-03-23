"""Remove obsolete socials application registry rows after retiring the app."""

from django.db import migrations

RETIRED_SOCIALS_APPLICATION_NAMES = ("apps.socials", "socials")


def remove_retired_socials_applications(apps, schema_editor):
    """Delete stale application rows that still point at the retired socials app.

    Args:
        apps: Historical app registry provided by Django migrations.
        schema_editor: Active schema editor for the migration run.

    Returns:
        None.
    """

    del schema_editor
    Application = apps.get_model("app", "Application")
    Application.objects.filter(name__in=RETIRED_SOCIALS_APPLICATION_NAMES).delete()


class Migration(migrations.Migration):
    """Remove obsolete socials application rows after retiring the app."""

    dependencies = [
        ("app", "0011_remove_retired_blog_application"),
    ]

    operations = [
        migrations.RunPython(
            remove_retired_socials_applications,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
