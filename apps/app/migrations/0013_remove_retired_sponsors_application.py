"""Remove obsolete sponsors application registry rows after retiring the app."""

from django.db import migrations

RETIRED_SPONSORS_APPLICATION_NAMES = ("apps.sponsors", "sponsors")


def remove_retired_sponsors_applications(apps, schema_editor):
    """Delete stale application rows that still point at the retired sponsors app.

    Args:
        apps: Historical app registry provided by Django migrations.
        schema_editor: Active schema editor for the migration run.

    Returns:
        None.
    """

    del schema_editor
    Application = apps.get_model("app", "Application")
    Application.objects.filter(name__in=RETIRED_SPONSORS_APPLICATION_NAMES).delete()


class Migration(migrations.Migration):
    """Remove obsolete sponsors application rows after retiring the app."""

    dependencies = [
        ("app", "0012_remove_retired_socials_application"),
    ]

    operations = [
        migrations.RunPython(
            remove_retired_sponsors_applications,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
