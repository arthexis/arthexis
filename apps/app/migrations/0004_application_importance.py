from django.db import migrations, models


CRITICAL_APPS = {"core", "ocpp", "pages", "release"}


def set_importance(apps, schema_editor):
    Application = apps.get_model("app", "Application")
    Application.objects.update(importance="baseline")
    Application.objects.filter(name__in=CRITICAL_APPS).update(importance="critical")


def noop_reverse(apps, schema_editor):
    """No reverse operation needed."""


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0003_applicationmodel_wiki_url"),
    ]

    operations = [
        migrations.AddField(
            model_name="application",
            name="importance",
            field=models.CharField(
                choices=[
                    ("critical", "Critical"),
                    ("baseline", "Baseline"),
                    ("prototype", "Prototype"),
                ],
                default="baseline",
                max_length=20,
            ),
        ),
        migrations.RunPython(set_importance, reverse_code=noop_reverse),
    ]
