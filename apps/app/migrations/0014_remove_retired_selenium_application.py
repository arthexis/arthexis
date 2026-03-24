"""Remove obsolete selenium application registry rows after retiring the app."""

from django.db import migrations

RETIRED_SELENIUM_APPLICATION_NAMES = ("apps.selenium", "selenium")


def remove_retired_selenium_applications(apps, schema_editor):
    """Delete stale application rows that still point at the retired selenium app.

    Args:
        apps: Historical app registry provided by Django migrations.
        schema_editor: Active schema editor for the migration run.

    Returns:
        None.
    """

    del schema_editor
    Application = apps.get_model("app", "Application")
    Application.objects.filter(name__in=RETIRED_SELENIUM_APPLICATION_NAMES).delete()


class Migration(migrations.Migration):
    """Remove obsolete selenium application rows after retiring the app."""

    dependencies = [
        ("app", "0013_remove_retired_sponsors_application"),
    ]

    operations = [
        migrations.RunPython(
            remove_retired_selenium_applications,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
