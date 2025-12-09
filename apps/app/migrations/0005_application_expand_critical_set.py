from django.db import migrations


CRITICAL_APPS = {
    "app",
    "celery",
    "core",
    "media",
    "nodes",
    "ocpp",
    "pages",
    "release",
}


def mark_additional_critical_apps(apps, schema_editor):
    Application = apps.get_model("app", "Application")
    Application.objects.filter(name__in=CRITICAL_APPS).update(importance="critical")


def noop_reverse(apps, schema_editor):
    """No reverse operation needed."""


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0004_application_importance"),
    ]

    operations = [
        migrations.RunPython(mark_additional_critical_apps, reverse_code=noop_reverse),
    ]
