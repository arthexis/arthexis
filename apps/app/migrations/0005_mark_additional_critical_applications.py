from django.db import migrations


CRITICAL_APPS = ["app", "celery", "media", "nodes"]


def mark_critical_apps(apps, schema_editor):
    Application = apps.get_model("app", "Application")
    Application.objects.filter(name__in=CRITICAL_APPS).update(importance="critical")


def unmark_critical_apps(apps, schema_editor):
    Application = apps.get_model("app", "Application")
    Application.objects.filter(name__in=CRITICAL_APPS).update(importance="baseline")


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0004_application_importance"),
    ]

    operations = [
        migrations.RunPython(mark_critical_apps, unmark_critical_apps),
    ]
