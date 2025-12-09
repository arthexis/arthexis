from django.db import migrations


CRITICAL_APPS = ["cards"]


def mark_critical_apps(apps, schema_editor):
    Application = apps.get_model("app", "Application")
    Application.objects.filter(name__in=CRITICAL_APPS).update(importance="critical")


def unmark_critical_apps(apps, schema_editor):
    Application = apps.get_model("app", "Application")
    Application.objects.filter(name__in=CRITICAL_APPS).update(importance="baseline")


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0005_mark_additional_critical_applications"),
    ]

    operations = [
        migrations.RunPython(mark_critical_apps, unmark_critical_apps),
    ]
