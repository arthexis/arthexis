from django.db import migrations, models


REFERENCE_APP_MAP = {
    "Django": "core",
    "Python": "core",
    "OCPP": "ocpp",
    "Celery": "celery",
    "Selenium": "core",
    "NGINX": "core",
    "PostgreSQL": "core",
    "SQLite": "core",
    "AWS LightSail": "core",
}


def assign_reference_applications(apps, schema_editor):
    """Assign matching seed references to an application by display title."""

    Application = apps.get_model("app", "Application")
    Reference = apps.get_model("links", "Reference")

    for alt_text, app_name in REFERENCE_APP_MAP.items():
        application = Application.objects.filter(name=app_name).first()
        if application is None:
            continue
        Reference.objects.filter(alt_text=alt_text).update(application=application)


def unassign_reference_applications(apps, schema_editor):
    """Remove application assignments created by this migration."""

    Reference = apps.get_model("links", "Reference")
    Reference.objects.filter(alt_text__in=REFERENCE_APP_MAP.keys()).update(
        application=None
    )


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0007_applicationmodel_ocpp_wiki_url"),
        ("links", "0015_alter_qrredirectlead_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="reference",
            name="application",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional application this reference belongs to.",
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="references",
                to="app.application",
            ),
        ),
        migrations.RunPython(
            assign_reference_applications,
            unassign_reference_applications,
        ),
    ]
