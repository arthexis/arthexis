from django.db import migrations, models


def mark_legacy_applications(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    Application = apps.get_model("app", "Application")
    Application.objects.using(db_alias).filter(
        name__icontains="legacy",
        importance="baseline",
    ).update(importance="legacy")


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="application",
            name="importance",
            field=models.CharField(
                choices=[
                    ("critical", "Critical"),
                    ("baseline", "Baseline"),
                    ("legacy", "Legacy"),
                    ("prototype", "Prototype"),
                ],
                default="baseline",
                max_length=20,
            ),
        ),
        migrations.RunPython(mark_legacy_applications, migrations.RunPython.noop),
    ]
